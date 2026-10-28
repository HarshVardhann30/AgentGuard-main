"""Optional Microsoft AutoGen adapter; all tool execution remains inside AgentGuard."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import threading
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from uuid import uuid4

from agent.tools import TOOL_REGISTRY
from security.guard import AgentGuard

EXPOSED_TOOLS = ("search_files", "read_file", "calculator", "write_file")


class AutoGenAdapterError(RuntimeError):
    """The optional provider, request, or protected tool invocation failed."""


class ProtectedOperationBusy(RuntimeError):
    """Raised when another protected operation still holds the execution gate."""


def normalize_tool_call(call: Any, allowed_tools: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """Normalize one AutoGen FunctionCall-like value into the guard request form."""
    if isinstance(call, Mapping):
        name, raw_arguments = call.get("name"), call.get("arguments")
    else:
        name = getattr(call, "name", None)
        raw_arguments = getattr(call, "arguments", None)
    if not isinstance(name, str) or name not in allowed_tools:
        raise ValueError("unknown tool; request denied")
    if isinstance(raw_arguments, str):
        try:
            arguments = json.loads(raw_arguments)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("tool arguments are not valid JSON; request denied") from exc
    else:
        arguments = raw_arguments
    if not isinstance(arguments, dict):
        raise ValueError("tool arguments must be a JSON object; request denied")
    schema = allowed_tools[name].get("parameters", {})
    if set(arguments) != set(schema):
        raise ValueError(f"{name} requires exactly the declared arguments; request denied")
    if any(not isinstance(arguments[key], str) for key in schema):
        raise ValueError("tool argument values must be strings; request denied")
    return name, arguments


@dataclass
class AutoGenRunResult:
    answer: str
    agent_id: str
    task_id: str
    session_id: str
    tool_calls: list[dict[str, Any]]
    completed: bool
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"answer": self.answer, "agent_id": self.agent_id, "task_id": self.task_id,
                "session_id": self.session_id, "tool_calls": self.tool_calls,
                "completed": self.completed, "error": self.error}


class AgentGuardAutoGenAdapter:
    """AutoGen proposal surface over the single authoritative AgentGuard.

    No implementation function is registered with AutoGen. Each registered
    FunctionTool wraps guard.execute, preserving review and audit semantics.
    """

    def __init__(self, *, agent_name: str = "agentguard_autogen", task_id: str | None = None,
                 session_id: str | None = None, guard: AgentGuard | None = None,
                 tools: Mapping[str, Any] | None = None, execution_timeout: float = 60.0):
        source = tools if tools is not None else TOOL_REGISTRY
        if not isinstance(execution_timeout, (int, float)) or execution_timeout <= 0:
            raise ValueError("execution_timeout must be positive")
        self.agent_id = f"autogen:{agent_name}"
        self.task_id = task_id or str(uuid4())
        self.session_id = session_id or str(uuid4())
        self.execution_timeout = float(execution_timeout)
        self.tools = {name: source[name] for name in EXPOSED_TOOLS if name in source}
        if set(self.tools) != set(EXPOSED_TOOLS):
            missing = sorted(set(EXPOSED_TOOLS) - set(self.tools))
            raise ValueError(f"required AgentGuard tools are unavailable: {', '.join(missing)}")
        self.guard = guard or AgentGuard(self.tools, agent_id=self.agent_id,
                                        task_id=self.task_id, session_id=self.session_id)
        # An injected authoritative guard still records this adapter's real
        # identity and request correlation, rather than its constructor defaults.
        self.guard.agent_id = self.agent_id
        self.guard.task_id = self.task_id
        self.guard.session_id = self.session_id
        self._function_tools: list[Any] | None = None
        self._calls: list[dict[str, Any]] = []
        self._required_tools: set[str] = set()
        self._tool_attempts: dict[str, int] = {}
        self._read_values: list[str] = []
        self._tool_inflight: dict[str, int] = {}
        self._batch_expected: dict[str, int] = {}
        self._batch_ready: dict[str, int] = {}
        self._inflight_lock = threading.Lock()
        # AutoGen runs a turn's tool calls concurrently (asyncio.gather). The
        # Ollama model client has no parallel_tool_calls switch, so serialize
        # protected executions here rather than allowing overlap or rejecting
        # otherwise valid concurrent proposals. A bounded wait fails closed.
        self._execution_gate = threading.Lock()

    async def _invoke(self, name: str, arguments: dict[str, Any]) -> str:
        proposal = {"name": name, "arguments": arguments}
        forced_block_reason = None
        tracked_tool = name if isinstance(name, str) and name in self.tools else None
        if tracked_tool:
            with self._inflight_lock:
                self._tool_inflight[tracked_tool] = self._tool_inflight.get(tracked_tool, 0) + 1
                self._batch_ready[tracked_tool] = self._batch_ready.get(tracked_tool, 0) + 1
                expected = self._batch_expected.get(tracked_tool, 0)
                if expected and self._batch_ready[tracked_tool] >= expected:
                    self._batch_expected.pop(tracked_tool, None)
                    self._batch_ready.pop(tracked_tool, None)
        try:
            tool_name, validated_arguments = normalize_tool_call(proposal, self.tools)
            self._tool_attempts[tool_name] = self._tool_attempts.get(tool_name, 0) + 1
            if self._tool_attempts[tool_name] > 2:
                forced_block_reason = f"{tool_name} retry limit reached; request denied"
            else:
                await asyncio.sleep(0)
                forced_block_reason = await self._workflow_precondition_failure(tool_name, validated_arguments)
        except ValueError as exc:
            forced_block_reason = str(exc)

        if forced_block_reason:
            # Invalid/unknown proposals and out-of-order workflow calls still
            # cross the authoritative guard and create a normal blocked audit row.
            try:
                denied = self.guard.execute(name, arguments, forced_block_reason=forced_block_reason)
                outcome = denied.to_dict()
            except Exception as audit_exc:
                outcome = {"tool": name, "status": "blocked", "decision": "block",
                           "executed": False, "audit_persisted": False,
                           "error": f"proposal denied; audit unavailable: {type(audit_exc).__name__}"}
            record = {"agent_id": self.agent_id, "task_id": self.task_id, "session_id": self.session_id,
                      "tool": name, "arguments": arguments, "execution": outcome}
            self._calls.append(record)
            self._finish_inflight(tracked_tool)
            return json.dumps(record, ensure_ascii=False, default=str)
        try:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + self.execution_timeout
            acquired = False
            while not acquired:
                acquired = self._execution_gate.acquire(blocking=False)
                if acquired:
                    break
                remaining = deadline - loop.time()
                if remaining <= 0:
                    # The proposal did not enter the executor. Persist a
                    # fail-closed decision through AgentGuard, including audit.
                    denied = self.guard.execute(
                        tool_name, validated_arguments,
                        forced_block_reason="protected execution wait timed out; request denied without execution",
                    )
                    outcome = denied.to_dict()
                    break
                await asyncio.sleep(min(0.01, remaining))
            if acquired:
                handed_off = False
                try:
                    def guarded_execute():
                        # Keep the gate until the real worker exits, including
                        # after asyncio reports an uncertain timeout.
                        try:
                            return self.guard.execute(tool_name, validated_arguments)
                        finally:
                            self._execution_gate.release()
                    handed_off = True
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        self._execution_gate.release()
                        handed_off = False
                        denied = self.guard.execute(
                            tool_name, validated_arguments,
                            forced_block_reason="protected execution wait timed out; request denied without execution",
                        )
                        outcome = denied.to_dict()
                    else:
                        execution = await asyncio.wait_for(
                            asyncio.to_thread(guarded_execute),
                            timeout=remaining,
                        )
                        outcome = execution.to_dict()
                finally:
                    if not handed_off and self._execution_gate.locked():
                        self._execution_gate.release()
        except asyncio.TimeoutError:
            # Cancellation of a synchronous protected tool may not stop its
            # worker thread; never claim that execution did not occur.
            outcome = {"tool": name, "status": "timeout_uncertain", "decision": "uncertain",
                       "executed": None, "audit_persisted": None,
                       "error": "tool execution timed out; reconcile its audit record before retrying"}
        except Exception as exc:
            outcome = {"tool": name, "status": "failed", "decision": "block", "executed": False,
                       "audit_persisted": False, "error": f"guard invocation failed: {type(exc).__name__}"}
        record = {"agent_id": self.agent_id, "task_id": self.task_id, "session_id": self.session_id,
                  "tool": name, "arguments": arguments, "execution": outcome}
        self._calls.append(record)
        self._finish_inflight(tracked_tool)
        return json.dumps(record, ensure_ascii=False, default=str)

    def _finish_inflight(self, name: str | None) -> None:
        if not name:
            return
        with self._inflight_lock:
            count = self._tool_inflight.get(name, 0)
            if count <= 1:
                self._tool_inflight.pop(name, None)
            else:
                self._tool_inflight[name] = count - 1

    def _make_wrapper(self, name: str, spec: Mapping[str, Any]) -> Callable[..., Any]:
        parameters = spec.get("parameters", {})
        expected_schemas = {
            "search_files": {"query": "string"},
            "read_file": {"filename": "string"},
            "calculator": {"expression": "string"},
            "write_file": {"filename": "string", "content": "string"},
        }
        if parameters != expected_schemas[name]:
            raise ValueError(f"{name} does not match its supported AgentGuard schema")

        if name == "search_files":
            async def invoke(query: str) -> str:
                return await self._invoke(name, {"query": query})
        elif name == "read_file":
            async def invoke(filename: str) -> str:
                return await self._invoke(name, {"filename": filename})
        elif name == "calculator":
            async def invoke(expression: str) -> str:
                return await self._invoke(name, {"expression": expression})
        else:
            async def invoke(filename: str, content: str) -> str:
                return await self._invoke(name, {"filename": filename, "content": content})

        invoke.__name__ = name
        invoke.__doc__ = str(spec.get("description", f"Guarded {name} operation."))
        return invoke

    async def _run_required_workflow(self, task: str, client: Any, tools: list[Any], timeout: float) -> str:
        """Use real AutoGen structured calls one at a time for operation-required tasks."""
        from autogen_core import CancellationToken
        from autogen_core.models import (AssistantMessage, FunctionExecutionResult,
                                         FunctionExecutionResultMessage, SystemMessage, UserMessage)

        by_name = {tool.name: tool for tool in tools}
        instructions = ("Use exactly the supplied AutoGen function for this step; do not emit textual JSON. "
                        "For each tool call use the exact declared argument names and values grounded in the "
                        "previous tool result. If it fails or returns denied/review/uncertain, stop and report "
                        "that outcome. Never invent file names, file contents, or arithmetic results.")
        messages: list[Any] = [SystemMessage(content=instructions), UserMessage(content=task, source="user")]
        answer = ""

        async def invoke(tool_name: str, prompt: str) -> dict[str, Any] | None:
            model_result = await asyncio.wait_for(
                client.create(messages=[*messages, UserMessage(content=prompt, source="user")],
                              tools=[by_name[tool_name].schema], tool_choice="required"),
                timeout=timeout)
            if not isinstance(model_result.content, list) or len(model_result.content) != 1:
                raise AutoGenAdapterError(f"AutoGen did not return a structured {tool_name} call")
            function_call = model_result.content[0]
            if getattr(function_call, "name", None) != tool_name:
                raise AutoGenAdapterError(f"AutoGen proposed an unexpected tool for required step {tool_name}")
            try:
                arguments = json.loads(function_call.arguments)
            except (TypeError, json.JSONDecodeError) as exc:
                raise AutoGenAdapterError(f"AutoGen returned malformed arguments for {tool_name}") from exc
            messages.append(AssistantMessage(content=[function_call], source=self.agent_id.replace(":", "_")))
            tool_result_text = await by_name[tool_name].run_json(
                arguments, CancellationToken(), call_id=function_call.id)
            execution = json.loads(tool_result_text)
            messages.append(FunctionExecutionResultMessage(content=[FunctionExecutionResult(
                content=tool_result_text, call_id=function_call.id, is_error=(
                    execution.get("execution", {}).get("status") != "allowed"), name=tool_name)]))
            if (execution.get("execution", {}).get("status") != "allowed"
                    or execution.get("execution", {}).get("executed") is not True
                    or execution.get("execution", {}).get("audit_persisted") is not True):
                return execution
            return execution

        if "search_files" in self._required_tools:
            result = await invoke("search_files", "Use search_files with a concise filename fragment that finds the requested report.")
            files = (((result or {}).get("execution") or {}).get("result") or {}).get("files", [])
            if not isinstance(files, list) or not files:
                raise AutoGenAdapterError("search_files found no permitted matching report")
            filename = files[0]
        else:
            filename = None

        if "read_file" in self._required_tools:
            prompt = (f"Call read_file with filename exactly {json.dumps(filename)} from the real search result."
                      if filename else "Call read_file using the exact filename explicitly requested by the user.")
            result = await invoke("read_file", prompt)
            execution = (result or {}).get("execution", {})
            if execution.get("status") != "allowed" or execution.get("executed") is not True:
                raise AutoGenAdapterError(f"read_file did not complete: {execution.get('error') or execution.get('status')}")
            content = (execution.get("result") or {}).get("content")
            if not isinstance(content, str):
                raise AutoGenAdapterError("read_file returned no text content")
            numbers = re.findall(r"(?<![\w.])-?\d+(?:\.\d+)?", content)
            # The task asks for sales total: monthly amounts are the numeric
            # lines with month labels; unrelated report years/metadata are omitted.
            values = []
            for line in content.splitlines():
                match = re.search(r"^\s*[^:]+:\s*(-?\d+(?:\.\d+)?)\s*$", line)
                if match:
                    values.append(match.group(1))
            if "calculator" in self._required_tools and not values:
                # Permit a plain numeric list but do not infer from prose text.
                values = [number for number in numbers]
            self._read_values = values
        if "calculator" in self._required_tools:
            if "read_file" not in self._required_tools:
                values = []
            else:
                values = getattr(self, "_read_values", [])
            if values:
                expression = "+".join(values)
                prompt = (f"Call calculator with the exact arithmetic expression {expression}. "
                          "Use only values extracted from the successful read_file result.")
            else:
                # Use natural-language user-provided arithmetic only for tasks
                # that explicitly request a calculation; file-based tasks never
                # calculate from missing/invalid read results.
                math_expression = re.search(r"(?:calculate|compute)\s+(.+?)(?:[?.!]|$)", task, re.I)
                if not values and math_expression and "read_file" not in self._required_tools:
                    prompt = f"Call calculator with this exact arithmetic expression: {math_expression.group(1).strip()}"
                elif not values and "read_file" in self._required_tools:
                    raise AutoGenAdapterError("calculator cannot proceed because no numeric values were read")
                else:
                    prompt = "Call calculator with the arithmetic expression for the explicitly requested calculation."
            result = await invoke("calculator", prompt)
            execution = (result or {}).get("execution", {})
            if execution.get("status") != "allowed" or execution.get("executed") is not True:
                raise AutoGenAdapterError(f"calculator did not complete: {execution.get('error') or execution.get('status')}")

        if "write_file" in self._required_tools:
            result = await invoke("write_file", "Use write_file with the safe new filename and text content explicitly requested by the user. Do not overwrite existing files.")
            execution = (result or {}).get("execution", {})
            if execution.get("status") == "review_required":
                return f"Write request is pending human review (approval {execution.get('approval_id')}). It has not executed."
            if execution.get("status") != "allowed" or execution.get("executed") is not True:
                raise AutoGenAdapterError(f"write_file did not complete: {execution.get('error') or execution.get('status')}")

        tool_summaries = []
        for call in self._calls:
            execution = call.get("execution", {})
            if execution.get("status") == "allowed" and execution.get("executed") is True:
                tool_summaries.append({"tool": call["tool"], "arguments": call["arguments"],
                                       "result": execution.get("result")})
            elif execution.get("status") == "review_required":
                tool_summaries.append({"tool": call["tool"], "status": "pending human review",
                                       "approval_id": execution.get("approval_id")})
        calculator = next((item for item in reversed(self._calls) if item.get("tool") == "calculator"), None)
        actual = (calculator or {}).get("execution", {}).get("result", {}).get("result")
        verified_results = "Verified AgentGuard tool results: " + json.dumps(tool_summaries, ensure_ascii=False)
        final_prompt = ("Answer the original request in one concise natural-language sentence using only these "
                        f"results. The actual calculator result is {actual!r}; include that exact value if a "
                        "calculation was requested. Do not call or imitate any tool.")
        for attempt in range(2):
            final = await asyncio.wait_for(client.create(
                messages=[*messages, UserMessage(content=verified_results, source="user"),
                          UserMessage(content=final_prompt, source="user")],
                tools=[], tool_choice="none"), timeout=timeout)
            answer = str(final.content) if isinstance(final.content, str) else ""
            if "calculator" not in self._required_tools or (actual is not None and str(actual) in answer.replace(",", "")):
                return answer
            final_prompt = (f"Your previous response omitted the real calculator result. Write one sentence that "
                            f"includes the exact value {actual!r} and is supported by the verified tool results. "
                            "Do not call or imitate tools.")
        raise AutoGenAdapterError("AutoGen final answer did not reflect the actual guarded calculator result")

    def _successful_tool_names(self) -> set[str]:
        return {call["tool"] for call in self._calls
                if call.get("execution", {}).get("status") == "allowed"
                and call.get("execution", {}).get("executed") is True
                and call.get("execution", {}).get("audit_persisted") is True}

    def _searched_filenames(self) -> set[str]:
        names: set[str] = set()
        for call in self._calls:
            execution = call.get("execution", {})
            if (call.get("tool") == "search_files" and execution.get("status") == "allowed"
                    and execution.get("executed") is True and execution.get("audit_persisted") is True):
                result = execution.get("result")
                if isinstance(result, dict) and isinstance(result.get("files"), list):
                    names.update(item for item in result["files"] if isinstance(item, str))
        return names

    def begin_tool_batch(self, calls: list[Any]) -> None:
        """Register only real structured calls from an AutoGen model response."""
        names = [getattr(call, "name", None) for call in calls]
        with self._inflight_lock:
            self._batch_expected = {name: names.count(name) for name in set(names)
                                    if isinstance(name, str) and name in self.tools}
            self._batch_ready = {name: 0 for name in self._batch_expected}

    async def _workflow_precondition_failure(self, tool: str, arguments: dict[str, Any]) -> str | None:
        dependency = None
        failure = None
        if tool == "read_file" and "search_files" in self._required_tools:
            dependency = "search_files"
            failure = "read_file denied: filename was not returned by a successful search_files call"
            if arguments.get("filename") in self._searched_filenames():
                return None
        elif tool == "calculator" and "read_file" in self._required_tools:
            dependency = "read_file"
            failure = "calculator denied: the required source file has not been successfully read"
            if "read_file" in self._successful_tool_names():
                return None
        else:
            return None

        # AutoGen 0.7.x invokes a batch of model-proposed FunctionCalls with
        # asyncio.gather. If the prerequisite is in that batch, allow it a
        # bounded opportunity to finish; never start a dependent side effect
        # based only on a textual claim or a merely proposed operation.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.execution_timeout
        while True:
            with self._inflight_lock:
                expected = self._batch_expected.get(dependency, 0)
                ready = self._batch_ready.get(dependency, 0)
                in_flight = self._tool_inflight.get(dependency, 0) > 0
                if expected and ready >= expected:
                    self._batch_expected.pop(dependency, None)
                    self._batch_ready.pop(dependency, None)
                    expected = 0
            if (not in_flight and not expected) or loop.time() >= deadline:
                return None if self._dependency_satisfied(tool) else failure
            await asyncio.sleep(min(0.01, max(0.0, deadline - loop.time())))

    def _dependency_satisfied(self, tool: str) -> bool:
        if tool == "read_file":
            return bool(self._searched_filenames())
        if tool == "calculator":
            return "read_file" in self._successful_tool_names()
        return True

    def create_tools(self) -> list[Any]:
        """Build official AutoGen tools whose raw arguments reach AgentGuard validation."""
        if self._function_tools is None:
            try:
                from autogen_core.tools import BaseTool
                from pydantic import ConfigDict, create_model
            except ImportError as exc:
                raise AutoGenAdapterError(
                    "Microsoft AutoGen is not installed; install the pinned AutoGen requirements."
                ) from exc

            class GuardedFunctionTool(BaseTool):
                def __init__(self, adapter, name, spec):
                    self.adapter = adapter
                    self.tool_name = name
                    self.argument_names = tuple(spec["parameters"])
                    fields = {key: (str, ...) for key in self.argument_names}
                    fields["model_config"] = (ConfigDict(extra="allow"), {})
                    args_type = create_model(f"{name.title().replace('_', '')}Arguments", **fields)
                    super().__init__(args_type, str, name,
                                     str(spec.get("description", f"Guarded {name} operation.")), strict=False)

                async def run(self, args, cancellation_token):
                    raw = args.model_dump(exclude={"model_config"})
                    # Preserve unexpected properties so normalize_tool_call and
                    # AgentGuard can reject and audit them rather than Pydantic
                    # silently dropping them before the security boundary.
                    return await self.adapter._invoke(self.tool_name, raw)

                async def run_json(self, args, cancellation_token, call_id=None):
                    # AutoGen's normal tool runner decodes raw JSON before this
                    # method. Preserve that structured call shape; schema and
                    # policy validation still happen inside adapter._invoke and
                    # then AgentGuard. Invalid JSON never reaches run_json and is
                    # reported by AutoGen as its own failed FunctionExecutionResult.
                    return await self.adapter._invoke(self.tool_name, args)

            self._function_tools = [GuardedFunctionTool(self, name, spec)
                                    for name, spec in self.tools.items()]
        return list(self._function_tools)

    async def run(self, task: str, *, model: str = "llama3.2:3b", timeout: float = 60.0) -> AutoGenRunResult:
        if not isinstance(task, str) or not task.strip():
            raise ValueError("task must be a non-empty string")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        try:
            from autogen_agentchat.agents import AssistantAgent
            from autogen_ext.models.ollama import OllamaChatCompletionClient
        except ImportError as exc:
            raise AutoGenAdapterError(
                "Microsoft AutoGen AgentChat/Ollama provider is unavailable; install requirements.txt."
            ) from exc
        client = OllamaChatCompletionClient(model=model, timeout=timeout,
                                            host=os.getenv("AGENTGUARD_OLLAMA_HOST", "http://127.0.0.1:11434"),
                                            model_info={"vision": False, "function_calling": True,
                                                        "json_output": False, "family": "unknown"})
        # AutoGen 0.7.5 executes all calls from one response concurrently.
        # Capture the actual structured FunctionCall list from the official
        # model client so dependency checks can wait only for a real same-batch
        # prerequisite. Text that imitates a function call never enters here.
        original_create = client.create

        async def tracked_create(*args, **kwargs):
            result = await original_create(*args, **kwargs)
            if isinstance(result.content, list):
                self.begin_tool_batch(result.content)
            return result

        client.create = tracked_create
        answer = ""
        error = None
        completed = False
        try:
            self._required_tools = required_tools_for_task(task)
            tools = self.create_tools()
            if self._required_tools:
                answer = await self._run_required_workflow(task, client, tools, timeout)
                missing = missing_required_tools(self._required_tools, self._calls)
                completed = not missing and bool(answer.strip())
                if missing:
                    error = "required AgentGuard tool operation(s) did not complete successfully: " + ", ".join(missing)
            else:
                agent = AssistantAgent(
                    name=self.agent_id.replace(":", "_"), model_client=client, tools=tools,
                    system_message=("You are an AgentGuard assistant. Use the supplied structured tools only "
                                   "when needed. Never fabricate tool results or present textual JSON as an "
                                   "executed call. Every tool proposal is independently validated and audited. "
                                   "Calculator accepts arithmetic expressions, not prose. Do not claim a "
                                   "pending, blocked, failed, timed-out, or unaudited action succeeded."),
                    reflect_on_tool_use=True, max_tool_iterations=8,
                )
                result = await asyncio.wait_for(agent.run(task=task), timeout=timeout)
                answer = str(getattr(result, "messages", [])[-1].content) if getattr(result, "messages", []) else ""
                completed = True
                if not self._calls and looks_like_unexecuted_tool_call(answer):
                    answer = "AutoGen returned text resembling a tool-call example, but no structured tool call was executed."
                    error = "unexecuted tool-call imitation returned as assistant text"
                    completed = False
        except asyncio.TimeoutError:
            answer = "AutoGen timed out; any tool outcome marked uncertain must be reconciled in the audit log."
            error = answer
        except Exception as exc:
            answer = "AutoGen request failed; no success is reported."
            error = f"{type(exc).__name__}: {str(exc)[:500]}"
        finally:
            await client.close()
        return AutoGenRunResult(answer, self.agent_id, self.task_id, self.session_id,
                                list(self._calls), completed, error)


def required_tools_for_task(task: str) -> set[str]:
    """Infer only explicit task verbs so a final text answer cannot mask a skipped operation."""
    lowered = task.casefold()
    required: set[str] = set()
    mentions_document = bool(re.search(r"\b(file|report|document)\b", lowered))
    asks_search = bool(re.search(r"\b(find|search|locate)\b", lowered)) and mentions_document
    asks_read = bool(re.search(r"\b(read|open|contents?)\b", lowered)) and mentions_document
    if asks_search:
        required.add("search_files")
    if asks_read or (asks_search and bool(re.search(r"\b(calculate|compute|total|sum|summari[sz]e)\b", lowered))):
        required.add("read_file")
    if re.search(r"\b(calculate|compute|multiply|product|sum|total)\b", lowered):
        required.add("calculator")
    if re.search(r"\b(write|create|save)\b", lowered) and mentions_document:
        required.add("write_file")
    return required


def missing_required_tools(required: set[str], calls: list[dict[str, Any]]) -> list[str]:
    """Return required operations with neither audited execution nor a valid review hold."""
    satisfied = set()
    for call in calls:
        execution = call.get("execution", {})
        if (execution.get("status") == "allowed" and execution.get("executed") is True
                and execution.get("audit_persisted") is True):
            satisfied.add(call.get("tool"))
        elif call.get("tool") == "write_file" and execution.get("status") == "review_required":
            # Review is a valid pending result, not successful execution.
            satisfied.add("write_file")
    return sorted(required - satisfied)


def looks_like_unexecuted_tool_call(answer: str) -> bool:
    """Detect a tool-shaped textual imitation, which must never masquerade as a real call."""
    return bool(re.search(r"[\"']name[\"']\s*:", answer, re.I)
                and re.search(r"[\"'](?:arguments|parameters)[\"']\s*:", answer, re.I)
                and re.search(r"\b(?:calculator|read_file|search_files|write_file)\b", answer, re.I))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", help="Task for the AutoGen agent")
    parser.add_argument("--model", default="llama3.2:3b", help="Local Ollama model name")
    parser.add_argument("--timeout", type=float, default=60.0, help="Provider and tool timeout in seconds")
    parser.add_argument("--agent-id", default="agentguard_autogen")
    parser.add_argument("--task-id")
    parser.add_argument("--session-id")
    args = parser.parse_args()
    try:
        adapter = AgentGuardAutoGenAdapter(agent_name=args.agent_id, task_id=args.task_id,
                                           session_id=args.session_id, execution_timeout=args.timeout)
        result = asyncio.run(adapter.run(args.task, model=args.model, timeout=args.timeout))
    except (ValueError, AutoGenAdapterError) as exc:
        parser.exit(1, f"AgentGuard AutoGen: {exc}\n")
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False, default=str))
    return 0 if result.completed else 1


if __name__ == "__main__":
    raise SystemExit(main())

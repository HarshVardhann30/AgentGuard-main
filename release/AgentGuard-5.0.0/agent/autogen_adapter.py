"""Optional Microsoft AutoGen adapter; all tool execution remains inside AgentGuard."""

from __future__ import annotations

import argparse
import asyncio
import json
import threading
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from uuid import uuid4

from agent.tools import TOOL_REGISTRY
from security.guard import AgentGuard

EXPOSED_TOOLS = ("read_file", "calculator", "write_file")


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
        # AutoGen runs a turn's tool calls concurrently (asyncio.gather), while
        # approval/audit state must stay strictly sequential. One protected
        # operation at a time; a second concurrent proposal is denied, never queued.
        self._execution_gate = threading.Lock()

    async def _invoke(self, name: str, arguments: dict[str, Any]) -> str:
        proposal = {"name": name, "arguments": arguments}
        try:
            tool_name, validated_arguments = normalize_tool_call(proposal, self.tools)
        except ValueError as exc:
            # Invalid/unknown proposals still cross the authoritative guard so
            # the denial is persisted in the normal JSONL/DB audit path. The
            # forced-block reason short-circuits before any tool lookup or call.
            try:
                denied = self.guard.execute(name, arguments, forced_block_reason=str(exc))
                outcome = denied.to_dict()
            except Exception as audit_exc:
                outcome = {"tool": name, "status": "blocked", "decision": "block",
                           "executed": False, "audit_persisted": False,
                           "error": f"invalid proposal denied; audit unavailable: {type(audit_exc).__name__}"}
            record = {"agent_id": self.agent_id, "task_id": self.task_id, "session_id": self.session_id,
                      "tool": name, "arguments": arguments, "execution": outcome}
            self._calls.append(record)
            return json.dumps(record, ensure_ascii=False, default=str)
        try:
            if not self._execution_gate.acquire(blocking=False):
                raise ProtectedOperationBusy(
                    "another protected operation is still executing; concurrent request denied")
            handed_off = False
            try:
                def guarded_execute():
                    # The worker thread owns the gate until the protected call
                    # truly finishes, so a timed-out call can never overlap a
                    # later one.
                    try:
                        return self.guard.execute(tool_name, validated_arguments)
                    finally:
                        self._execution_gate.release()
                handed_off = True
                execution = await asyncio.wait_for(
                    asyncio.to_thread(guarded_execute),
                    timeout=self.execution_timeout,
                )
            finally:
                if not handed_off:
                    self._execution_gate.release()
            outcome = execution.to_dict()
        except asyncio.TimeoutError:
            # Cancellation of a synchronous protected tool may not stop its
            # worker thread; never claim that execution did not occur.
            outcome = {"tool": name, "status": "timeout_uncertain", "decision": "uncertain",
                       "executed": None, "audit_persisted": None,
                       "error": "tool execution timed out; reconcile its audit record before retrying"}
        except ProtectedOperationBusy as exc:
            outcome = {"tool": name, "status": "failed", "decision": "block", "executed": False,
                       "audit_persisted": False, "error": str(exc)}
        except Exception as exc:
            outcome = {"tool": name, "status": "failed", "decision": "block", "executed": False,
                       "audit_persisted": False, "error": f"guard invocation failed: {type(exc).__name__}"}
        record = {"agent_id": self.agent_id, "task_id": self.task_id, "session_id": self.session_id,
                  "tool": name, "arguments": arguments, "execution": outcome}
        self._calls.append(record)
        return json.dumps(record, ensure_ascii=False, default=str)

    def _make_wrapper(self, name: str, spec: Mapping[str, Any]) -> Callable[..., Any]:
        parameters = spec.get("parameters", {})
        expected_schemas = {
            "read_file": {"filename": "string"},
            "calculator": {"expression": "string"},
            "write_file": {"filename": "string", "content": "string"},
        }
        if parameters != expected_schemas[name]:
            raise ValueError(f"{name} does not match its supported AgentGuard schema")

        if name == "read_file":
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

    def create_tools(self) -> list[Any]:
        """Build official AutoGen FunctionTools over adapter-owned wrappers."""
        if self._function_tools is None:
            try:
                from autogen_core.tools import FunctionTool
            except ImportError as exc:
                raise AutoGenAdapterError(
                    "Microsoft AutoGen is not installed; install the pinned AutoGen requirements."
                ) from exc
            self._function_tools = [
                FunctionTool(self._make_wrapper(name, spec), description=str(spec.get("description", name)),
                             name=name, strict=False)
                for name, spec in self.tools.items()
            ]
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
                                            model_info={"vision": False, "function_calling": True,
                                                        "json_output": False, "family": "unknown"})
        answer = ""
        error = None
        completed = False
        try:
            agent = AssistantAgent(
                name=self.agent_id.replace(":", "_"), model_client=client,
                tools=self.create_tools(),
                system_message=("You are an AgentGuard assistant. You may only use the supplied tools, one tool "
                               "call at a time. Tool requests are proposals and AgentGuard independently "
                               "validates every request. A denial because another operation is executing has no "
                               "side effect, so you may issue that request again once it completes. Never claim a "
                               "pending, blocked, failed, timed-out, or unaudited operation succeeded. Do not "
                               "retry uncertain side effects."),
                reflect_on_tool_use=True, max_tool_iterations=8,
            )
            result = await asyncio.wait_for(agent.run(task=task), timeout=timeout)
            answer = str(getattr(result, "messages", [])[-1].content) if getattr(result, "messages", []) else ""
            completed = True
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

import asyncio
import json
import threading
import time

import pytest

from agent.autogen_adapter import (AgentGuardAutoGenAdapter, looks_like_unexecuted_tool_call,
                                   missing_required_tools, normalize_tool_call, required_tools_for_task)
from agent.tools import TOOL_REGISTRY
from security.guard import AgentGuard
from security.logger import AuditLogger


class SafePredictor:
    def predict(self, *_args):
        return {"risk_score": 0, "probability": 0.0, "model_version": "test-model"}


def make_adapter(tmp_path, **kwargs):
    tools = dict(TOOL_REGISTRY)
    guard = AgentGuard(tools, logger=AuditLogger(tmp_path / "audit.jsonl"), predictor=SafePredictor())
    return AgentGuardAutoGenAdapter(guard=guard, task_id="task-test", session_id="session-test", **kwargs)


def test_normalizes_autogen_functioncall_and_json_arguments():
    proposal = type("FunctionCall", (), {"name": "calculator", "arguments": '{"expression":"6*7"}'})()
    assert normalize_tool_call(proposal, TOOL_REGISTRY) == ("calculator", {"expression": "6*7"})


@pytest.mark.parametrize("call", [
    {"name": "shell", "arguments": {}},
    {"name": "calculator", "arguments": "not-json"},
    {"name": "calculator", "arguments": {"expression": "1+1", "extra": "x"}},
    {"name": "calculator", "arguments": {"expression": 4}},
    {"name": "read_file", "arguments": {}},
])
def test_rejects_unknown_and_invalid_tool_proposals(call):
    with pytest.raises(ValueError):
        normalize_tool_call(call, TOOL_REGISTRY)


def test_registered_autogen_tools_are_adapter_wrappers_not_raw_functions(tmp_path):
    adapter = make_adapter(tmp_path)
    tools = adapter.create_tools()
    assert {tool.name for tool in tools} == {"search_files", "read_file", "calculator", "write_file"}
    for tool in tools:
        assert tool.adapter is adapter
        assert not hasattr(tool, "_func")
    for tool in tools:
        assert set(tool.schema.get("parameters", {}).get("properties", {})) == set(adapter.tools[tool.name]["parameters"])
        assert tool.schema["parameters"]["additionalProperties"] is False


def test_search_read_calculate_chain_uses_actual_guarded_results(tmp_path, documents):
    adapter = make_adapter(tmp_path)
    adapter._required_tools = required_tools_for_task("Find the sales report, read it, and calculate total sales.")

    adapter.begin_tool_batch([type("Call", (), {"name": "search_files"})()])
    search = json.loads(asyncio.run(adapter._invoke("search_files", {"query": "sales"})))
    assert search["execution"]["status"] == "allowed"
    assert search["execution"]["result"] == {"files": ["sales_report.txt"]}

    read = json.loads(asyncio.run(adapter._invoke("read_file", {"filename": "sales_report.txt"})))
    assert read["execution"]["status"] == "allowed"
    assert "12000" in read["execution"]["result"]["content"]

    calculation = json.loads(asyncio.run(adapter._invoke("calculator", {"expression": "12000+15000+18000+21000"})))
    assert calculation["execution"]["status"] == "allowed"
    assert calculation["execution"]["result"] == {"result": 66000}
    assert missing_required_tools(adapter._required_tools, adapter._calls) == []
    assert all(call["execution"]["audit_persisted"] is True for call in adapter._calls)
    assert all(call["agent_id"] == adapter.agent_id and call["task_id"] == adapter.task_id
               and call["session_id"] == adapter.session_id for call in adapter._calls)


def test_calculator_is_denied_before_required_file_is_read(tmp_path, documents):
    adapter = make_adapter(tmp_path)
    adapter._required_tools = required_tools_for_task("Find the sales report and calculate the total.")
    result = json.loads(asyncio.run(adapter._invoke("calculator", {"expression": "12000+15000"})))
    assert result["execution"]["status"] == "blocked"
    assert result["execution"]["executed"] is False
    assert "successfully read" in result["execution"]["error"]


def test_read_requires_filename_returned_by_guarded_search(tmp_path, documents):
    adapter = make_adapter(tmp_path)
    adapter._required_tools = required_tools_for_task("Find the sales report and read it.")
    result = json.loads(asyncio.run(adapter._invoke("read_file", {"filename": "sales_report.txt"})))
    assert result["execution"]["status"] == "blocked"
    assert result["execution"]["executed"] is False
    assert "not returned" in result["execution"]["error"]


def test_failed_search_and_missing_file_do_not_satisfy_required_steps(tmp_path, documents):
    adapter = make_adapter(tmp_path)
    adapter._required_tools = required_tools_for_task("Find the sales report and calculate total sales.")
    search = json.loads(asyncio.run(adapter._invoke("search_files", {"query": "missing"})))
    assert search["execution"]["result"] == {"files": []}
    read = json.loads(asyncio.run(adapter._invoke("read_file", {"filename": "sales_report.txt"})))
    assert read["execution"]["status"] == "blocked"
    assert missing_required_tools(adapter._required_tools, adapter._calls) == ["calculator", "read_file"]


def test_tool_call_imitation_is_never_treated_as_an_execution():
    assert looks_like_unexecuted_tool_call('{"name":"calculator","parameters":""}')
    assert not looks_like_unexecuted_tool_call("The calculation result is 6300.")


def test_required_action_completion_uses_audited_success_not_text():
    required = required_tools_for_task("Calculate 70 * 90.")
    assert required == {"calculator"}
    assert missing_required_tools(required, []) == ["calculator"]
    assert missing_required_tools(required, [{"tool": "calculator", "execution": {
        "status": "allowed", "executed": True, "audit_persisted": False}}]) == ["calculator"]


def test_required_workflow_uses_real_autogen_tool_schemas_and_guarded_results(tmp_path, documents, monkeypatch):
    adapter = make_adapter(tmp_path)
    adapter._required_tools = required_tools_for_task("Calculate 70 * 90.")
    tools = adapter.create_tools()
    calculator = next(tool for tool in tools if tool.name == "calculator")
    seen = {}

    class Client:
        async def create(self, *, messages, tools, tool_choice=None):
            if tools:
                from autogen_core import FunctionCall
                seen["schema"] = tools[0]
                seen["choice"] = tool_choice
                result = type("ModelResult", (), {})()
                result.content = [FunctionCall(
                    name="calculator", arguments='{"expression":"70*90"}', id="call-1")]
                return result
            result = type("ModelResult", (), {})()
            result.content = "The calculation is 6300."
            return result

    async def workflow():
        return await adapter._run_required_workflow("Calculate 70 * 90.", Client(), [calculator], 5)

    answer = asyncio.run(workflow())
    assert seen["schema"]["name"] == "calculator"
    assert seen["choice"] == "required"
    assert answer
    assert adapter._calls[0]["execution"]["result"] == {"result": 6300}
    assert adapter._calls[0]["execution"]["executed"] is True
    assert adapter._calls[0]["execution"]["audit_persisted"] is True


def test_adapter_executes_using_existing_guard_and_propagates_identity(tmp_path, documents):
    adapter = make_adapter(tmp_path)
    result = json.loads(asyncio.run(adapter._invoke("calculator", {"expression": "6*7"})))
    assert result["execution"]["status"] == "allowed"
    assert result["execution"]["result"] == {"result": 42}
    assert result["agent_id"] == "autogen:agentguard_autogen"
    assert result["task_id"] == "task-test"
    assert result["session_id"] == "session-test"
    assert result["execution"]["executed"] is True
    assert result["execution"]["audit_persisted"] is True
    assert adapter.guard.agent_id == result["agent_id"]


def test_write_file_review_is_not_executed_by_adapter(tmp_path, documents):
    from backend.database import persist_action

    calls = []
    guard = AgentGuard(dict(TOOL_REGISTRY), logger=AuditLogger(tmp_path / "audit.jsonl", persist=persist_action),
                       predictor=SafePredictor())
    adapter = AgentGuardAutoGenAdapter(guard=guard, task_id="task-test", session_id="session-test")
    result = json.loads(asyncio.run(adapter._invoke("write_file", {"filename": "review.txt", "content": "synthetic"})))
    assert result["execution"]["status"] == "review_required"
    assert result["execution"]["executed"] is False
    assert not (documents / "review.txt").exists()
    assert result["execution"]["approval_id"]


def test_direct_adapter_invocation_cannot_bypass_guard_validation(tmp_path, documents):
    adapter = make_adapter(tmp_path)
    adapter._required_tools = set()
    result = json.loads(asyncio.run(adapter._invoke("read_file", {"filename": "../outside.txt"})))
    assert result["execution"]["status"] == "blocked"
    assert result["execution"]["executed"] is False
    assert not (tmp_path / "outside.txt").exists()


def test_unknown_and_malformed_proposals_are_audited_by_guard(tmp_path):
    adapter = make_adapter(tmp_path)
    unknown = json.loads(asyncio.run(adapter._invoke("shell", {"cmd": "do not run"})))
    malformed = json.loads(asyncio.run(adapter._invoke(
        "calculator", {"expression": "1+1", "extra": "unexpected"})))

    assert [unknown["execution"]["status"], malformed["execution"]["status"]] == ["blocked", "blocked"]
    assert all(item["execution"]["decision"] == "block" for item in (unknown, malformed))
    assert all(item["execution"]["executed"] is False for item in (unknown, malformed))
    assert all(item["execution"]["audit_persisted"] is True for item in (unknown, malformed))
    rows = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(row["tool"], row["status"], row["executed"]) for row in rows] == [
        ("shell", "blocked", False), ("calculator", "blocked", False)]


def test_wrapper_timeout_reports_uncertain_side_effect(tmp_path, monkeypatch):
    adapter = make_adapter(tmp_path, execution_timeout=0.01)

    def delayed(*_args, **_kwargs):
        time.sleep(0.1)
        return type("Execution", (), {"to_dict": lambda self: {"status": "allowed"}})()

    monkeypatch.setattr(adapter.guard, "execute", delayed)
    result = json.loads(asyncio.run(adapter._invoke("calculator", {"expression": "1+1"})))
    assert result["execution"]["status"] == "timeout_uncertain"
    assert result["execution"]["executed"] is None
    time.sleep(0.15)  # Let the fake worker exit before test teardown.


def test_original_ollama_agent_remains_unchanged_and_callable():
    from agent.agent import Agent

    assert Agent().guard.tools is not None
    assert Agent().guard.tools.keys() == TOOL_REGISTRY.keys()


def test_concurrent_proposals_are_serialized_within_guard_boundary(tmp_path, monkeypatch):
    """AutoGen gathers calls; valid requests serialize, and each crosses guard."""
    adapter = make_adapter(tmp_path, execution_timeout=5)
    entered = threading.Event()
    unblock = threading.Event()
    real_execute = adapter.guard.execute
    concurrent = []
    active = 0
    lock = threading.Lock()

    def slow_execute(tool, arguments, **kwargs):
        nonlocal active
        with lock:
            active += 1
            concurrent.append(active)
        if arguments["expression"] == "1+1":
            entered.set()
            assert unblock.wait(2), "first execution was never released"
        try:
            return real_execute(tool, arguments, **kwargs)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(adapter.guard, "execute", slow_execute)

    async def run():
        first = asyncio.create_task(adapter._invoke("calculator", {"expression": "1+1"}))
        assert await asyncio.to_thread(entered.wait, 2)
        second_task = asyncio.create_task(adapter._invoke("calculator", {"expression": "2+2"}))
        await asyncio.sleep(0.05)
        unblock.set()
        first_result, second_result = await asyncio.gather(first, second_task)
        return json.loads(first_result), json.loads(second_result)

    first_result, second_result = asyncio.run(run())
    assert first_result["execution"]["status"] == "allowed"
    assert first_result["execution"]["executed"] is True
    assert second_result["execution"]["status"] == "allowed"
    assert second_result["execution"]["executed"] is True
    assert max(concurrent) == 1
    assert first_result["execution"]["audit_persisted"] is True
    assert second_result["execution"]["audit_persisted"] is True

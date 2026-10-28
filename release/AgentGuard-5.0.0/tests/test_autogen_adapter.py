import asyncio
import json
import threading
import time

import pytest

from agent.autogen_adapter import AgentGuardAutoGenAdapter, normalize_tool_call
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
    assert {tool.name for tool in tools} == {"read_file", "calculator", "write_file"}
    for tool in tools:
        assert tool._func is not adapter.tools[tool.name]["function"]
        assert tool._func.__module__ == "agent.autogen_adapter"
    for tool in tools:
        assert set(tool.schema.get("parameters", {}).get("properties", {})) == set(adapter.tools[tool.name]["parameters"])
        assert tool.schema["parameters"]["additionalProperties"] is False


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


def test_concurrent_proposals_are_serialized_and_second_request_is_denied(tmp_path, monkeypatch):
    """AutoGen gathers a turn's tool calls; the protected gate must deny the
    concurrent proposal instead of running two guard executions at once."""
    adapter = make_adapter(tmp_path, execution_timeout=5)
    entered = threading.Event()
    unblock = threading.Event()
    real_execute = adapter.guard.execute

    def slow_execute(tool, arguments):
        entered.set()
        assert unblock.wait(2), "first execution was never released"
        return real_execute(tool, arguments)

    monkeypatch.setattr(adapter.guard, "execute", slow_execute)

    async def run():
        first = asyncio.create_task(adapter._invoke("calculator", {"expression": "1+1"}))
        assert await asyncio.to_thread(entered.wait, 2)
        second = json.loads(await adapter._invoke("calculator", {"expression": "2+2"}))
        unblock.set()
        first_result = json.loads(await first)
        return second, first_result

    second, first_result = asyncio.run(run())
    assert first_result["execution"]["status"] == "allowed"
    assert first_result["execution"]["executed"] is True
    assert second["execution"]["status"] == "failed"
    assert second["execution"]["decision"] == "block"
    assert second["execution"]["executed"] is False
    assert "still executing" in second["execution"]["error"]

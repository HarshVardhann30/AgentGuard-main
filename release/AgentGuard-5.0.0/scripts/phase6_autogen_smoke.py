"""Live Microsoft AutoGen smoke test against the authoritative AgentGuard boundary.

Everything runs in a disposable workspace: a temporary working directory (so
`logs/` writes are disposable), a temporary SQLite database, and synthetic
documents. The project database, logs, and documents are never touched.

Part A drives a real Ollama-backed AutoGen conversation through the adapter.
Part B exercises the protected workflow through the adapter's registered
AutoGen FunctionTool wrappers (the exact call path AutoGen uses), covering
ALLOW, REVIEW + approve/reject/invalid token, BLOCK, unknown tools, and
malformed arguments, then audits the persisted records.

Usage:
    python -m scripts.phase6_autogen_smoke
    python -m scripts.phase6_autogen_smoke --skip-live
    python -m scripts.phase6_autogen_smoke --model llama3.2:3b --timeout 240
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="llama3.2:3b", help="Local Ollama model for the live conversation")
    parser.add_argument("--timeout", type=float, default=240.0, help="Live provider/timeout budget in seconds")
    parser.add_argument("--skip-live", action="store_true", help="Run only the protected-workflow checks")
    parser.add_argument("--output", default="reports/phase6_autogen_smoke.json",
                        help="Where to write the machine-readable result (relative to the invoking directory)")
    args = parser.parse_args()

    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = Path.cwd() / output_path

    workdir = Path(tempfile.mkdtemp(prefix="agentguard-autogen-smoke-"))
    os.chdir(workdir)  # logs/ and any relative write stay inside the disposable workspace

    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, detail: Any = "", group: str = "workflow") -> None:
        checks.append({"group": group, "check": name, "passed": bool(passed), "detail": str(detail)})
        print(f"[{'PASS' if passed else 'FAIL'}] ({group}) {name}: {detail}", flush=True)

    live: dict[str, Any] = {"attempted": False, "completed": False, "answer": "", "error": None,
                            "elapsed_ms": None, "tool_calls": [], "model": args.model}
    started_at = datetime.now(timezone.utc).isoformat()

    from backend.database import configure_database, get_session, init_db, persist_action
    from backend.models import ActionRecord
    import agent.tools as file_tools
    from agent.autogen_adapter import AgentGuardAutoGenAdapter, normalize_tool_call
    from agent.tools import TOOL_REGISTRY
    from security.guard import AgentGuard
    from security.logger import AuditLogger

    docs = workdir / "documents"
    docs.mkdir()
    (docs / "sales_report.txt").write_text(
        "January: 12000\nFebruary: 15000\nMarch: 18000\nApril: 21000\nBest region: North India\n",
        encoding="utf-8")
    (docs / "secrets.txt").write_text("SYNTHETIC_SECRET=not-a-credential\n", encoding="utf-8")
    (docs / "outside.txt").write_text("synthetic outside sentinel\n", encoding="utf-8")
    file_tools.DOCUMENT_DIR = docs
    configure_database(f"sqlite:///{(workdir / 'smoke.db').as_posix()}")
    init_db()

    # ---------------------------------------------------------------- Part A
    if args.skip_live:
        live["error"] = "skipped via --skip-live"
        check("live_conversation_completed", True, "skipped via --skip-live", group="live")
    else:
        adapter = AgentGuardAutoGenAdapter(agent_name="agentguard_autogen")
        task = ("Call read_file with filename sales_report.txt to read the file. Then call calculator once with "
                "the expression exactly 12000+15000+18000+21000. Answer with only the resulting total.")
        live["attempted"] = True
        begin = time.perf_counter()
        try:
            result = asyncio.run(adapter.run(task, model=args.model, timeout=args.timeout))
            live["elapsed_ms"] = round((time.perf_counter() - begin) * 1000, 1)
            live["completed"] = result.completed
            live["answer"] = result.answer
            live["error"] = result.error
            live["tool_calls"] = [
                {"tool": call["tool"],
                 "status": call["execution"].get("status"),
                 "decision": call["execution"].get("decision"),
                 "executed": call["execution"].get("executed"),
                 "action_id": call["execution"].get("action_id"),
                 "audit_persisted": call["execution"].get("audit_persisted"),
                 "arguments": call.get("arguments"),
                 "error": call["execution"].get("error"),
                 "result": str(call["execution"].get("result"))[:200]}
                for call in result.tool_calls]
            check("live_conversation_completed", result.completed,
                  result.error or f"{len(result.tool_calls)} tool call(s), answer={result.answer[:120]!r}",
                  group="live")
            executed_calls = [call for call in live["tool_calls"]
                              if call["executed"] is True and call["audit_persisted"]]
            check("live_tool_call_reached_guard_and_executed", bool(executed_calls),
                  json.dumps(live["tool_calls"]), group="live")
            succeeded = [call for call in live["tool_calls"] if call["status"] == "allowed"]
            check("live_tool_execution_succeeded", bool(succeeded),
                  json.dumps([{"arguments": call["arguments"], "status": call["status"],
                               "error": call["error"]} for call in live["tool_calls"]]), group="live")

            session = get_session()
            try:
                rows = session.query(ActionRecord).filter(ActionRecord.task_id == adapter.task_id).all()
            finally:
                session.close()
            check("live_audit_records_exist", bool(rows), f"{len(rows)} record(s) for task {adapter.task_id}",
                  group="live")
            identity_ok = bool(rows) and all(
                row.agent_id == adapter.agent_id and row.session_id == adapter.session_id for row in rows)
            check("live_identity_and_session_correlation", identity_ok,
                  f"agent_id={adapter.agent_id} session_id={adapter.session_id} task_id={adapter.task_id}",
                  group="live")
            reported_ids = {call["action_id"] for call in live["tool_calls"] if call.get("action_id")}
            persisted_ids = {row.action_id for row in rows}
            check("live_audit_matches_reported_executions", reported_ids <= persisted_ids,
                  f"matched={len(reported_ids & persisted_ids)} missing={sorted(reported_ids - persisted_ids)}",
                  group="live")
        except Exception as exc:  # provider unavailable, import failure, etc.
            live["error"] = f"{type(exc).__name__}: {exc}"
            live["elapsed_ms"] = round((time.perf_counter() - begin) * 1000, 1)
            check("live_conversation_completed", False, live["error"], group="live")

    # ---------------------------------------------------------------- Part B
    approver_token = "phase6-smoke-approver-token"
    guard = AgentGuard(dict(TOOL_REGISTRY),
                       logger=AuditLogger(workdir / "workflow_audit.jsonl", persist=persist_action),
                       agent_id="smoke:setup", session_id="smoke-session", task_id="smoke-workflow",
                       approver_token=approver_token)
    adapter2 = AgentGuardAutoGenAdapter(guard=guard, task_id="smoke-workflow", session_id="smoke-session",
                                         execution_timeout=30)
    tools_by_name = {tool.name: tool for tool in adapter2.create_tools()}

    async def through_autogen_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        # This is exactly the callable AutoGen invokes for a FunctionTool.
        return json.loads(await tools_by_name[name]._func(**arguments))

    def invoke(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return asyncio.run(through_autogen_tool(name, arguments))

    permitted = invoke("read_file", {"filename": "sales_report.txt"})
    execution = permitted["execution"]
    check("permitted_read_allowed_and_executed",
          execution.get("status") == "allowed" and execution.get("executed") is True
          and "12000" in str(execution.get("result", "")),
          json.dumps({k: execution.get(k) for k in ("status", "decision", "executed", "audit_persisted")}))

    try:
        normalize_tool_call({"name": "shell", "arguments": {"cmd": "dir"}}, adapter2.tools)
        unknown_rejected = False
        unknown_detail = "normalize_tool_call returned instead of raising"
    except ValueError as exc:
        unknown_rejected = True
        unknown_detail = str(exc)
    check("unknown_tool_rejected", unknown_rejected, unknown_detail)
    unknown_call = json.loads(asyncio.run(adapter2._invoke("shell", {"cmd": "dir"})))
    check("unknown_tool_through_autogen_denied_without_execution",
          unknown_call["execution"].get("executed") is False
          and unknown_call["execution"].get("status") in {"failed", "blocked"},
          json.dumps(unknown_call["execution"]))

    malformed = json.loads(asyncio.run(adapter2._invoke(
        "calculator", {"expression": "1+1", "extra": "x"})))
    check("malformed_arguments_denied_without_execution",
          malformed["execution"].get("executed") is False
          and malformed["execution"].get("status") in {"failed", "blocked"},
          json.dumps(malformed["execution"]))

    review = invoke("write_file", {"filename": "smoke_review.txt", "content": "synthetic review payload"})
    review_execution = review["execution"]
    approval_id = review_execution.get("approval_id")
    check("write_reaches_review_and_is_not_executed",
          review_execution.get("status") == "review_required" and review_execution.get("executed") is False
          and bool(approval_id) and not (docs / "smoke_review.txt").exists(),
          json.dumps({k: review_execution.get(k) for k in ("status", "executed", "approval_id")}))

    rejected = guard.resolve_approval(approval_id, "reject", "phase6-smoke", approver_token)
    check("rejected_approval_does_not_execute",
          rejected.get("status") == "rejected" and rejected.get("executed") is False
          and not (docs / "smoke_review.txt").exists(),
          json.dumps(rejected))

    review2 = invoke("write_file", {"filename": "smoke_review.txt", "content": "synthetic review payload"})
    invalid = guard.resolve_approval(review2["execution"].get("approval_id"), "approve", "phase6-smoke",
                                     "wrong-token")
    check("invalid_approval_token_does_not_execute",
          invalid.get("status") == "unauthorized" and invalid.get("executed") is False
          and not (docs / "smoke_review.txt").exists(),
          json.dumps(invalid))

    review3 = invoke("write_file", {"filename": "smoke_review.txt", "content": "synthetic review payload"})
    approved = guard.resolve_approval(review3["execution"].get("approval_id"), "approve", "phase6-smoke",
                                      approver_token)
    written = (docs / "smoke_review.txt")
    check("valid_approval_executes_after_revalidation",
          approved.get("status") == "allowed" and approved.get("executed") is True
          and written.exists() and written.read_text(encoding="utf-8") == "synthetic review payload",
          json.dumps(approved))

    traversal = invoke("read_file", {"filename": "../outside.txt"})
    check("block_traversal_has_no_side_effect",
          traversal["execution"].get("status") == "blocked" and traversal["execution"].get("executed") is False,
          json.dumps({k: traversal["execution"].get(k) for k in ("status", "decision", "executed")}))
    secret = invoke("read_file", {"filename": "secrets.txt"})
    check("block_protected_path_has_no_side_effect",
          secret["execution"].get("status") == "blocked" and secret["execution"].get("executed") is False,
          json.dumps({k: secret["execution"].get(k) for k in ("status", "decision", "executed")}))

    session = get_session()
    try:
        workflow_rows = session.query(ActionRecord).filter(ActionRecord.task_id == "smoke-workflow").all()
    finally:
        session.close()
    executed_rows = [row for row in workflow_rows if row.executed]
    check("workflow_audit_reflects_actual_execution",
          len(workflow_rows) >= 7 and len(executed_rows) == 2
          and {row.status for row in executed_rows} == {"allowed"}
          and all(row.agent_id == adapter2.agent_id for row in workflow_rows)
          and all(row.session_id == "smoke-session" for row in workflow_rows),
          f"records={len(workflow_rows)} executed={[ (row.tool, row.status) for row in executed_rows ]}")

    audit_jsonl = workdir / "workflow_audit.jsonl"
    jsonl_lines = [line for line in audit_jsonl.read_text(encoding="utf-8").splitlines() if line.strip()] \
        if audit_jsonl.exists() else []
    check("workflow_jsonl_matches_database", len(jsonl_lines) == len(workflow_rows),
          f"jsonl={len(jsonl_lines)} db={len(workflow_rows)}")

    # ------------------------------------------------------------- reporting
    security_checks = [item for item in checks if item["group"] == "workflow"]
    live_checks = [item for item in checks if item["group"] == "live"]
    summary = {
        "generated_at": started_at,
        "model": args.model,
        "live_attempted": live["attempted"],
        "live_completed": live["completed"],
        "live_status": ("passed" if all(item["passed"] for item in live_checks) and live["attempted"]
                        and not args.skip_live else ("skipped" if args.skip_live else "failed")),
        "security_checks_passed": all(item["passed"] for item in security_checks),
        "security_check_count": len(security_checks),
        "live_check_count": len(live_checks),
        "checks_passed": sum(item["passed"] for item in checks),
        "checks_total": len(checks),
        "disposable_workspace": str(workdir),
        "live": live,
        "checks": checks,
        "limitations": [
            "Synthetic disposable documents and an isolated temporary SQLite database only.",
            "Part B drives the adapter's registered AutoGen FunctionTool callables directly; the live model is "
            "not relied on to reproduce each approval branch deterministically.",
            "Part A depends on a local Ollama daemon and model availability.",
        ],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"live_status": summary["live_status"],
                      "security_checks_passed": summary["security_checks_passed"],
                      "checks_passed": summary["checks_passed"],
                      "checks_total": summary["checks_total"],
                      "live_completed": summary["live_completed"],
                      "live_error": summary["live"]["error"]}, indent=2), flush=True)
    print(f"result written to {output_path}", flush=True)
    all_passed = summary["checks_passed"] == summary["checks_total"]
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

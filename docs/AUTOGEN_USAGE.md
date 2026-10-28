# Using Microsoft AutoGen in AgentGuard Chat

AutoGen is an additional agent option in the existing Chat page. It uses Microsoft AutoGen
AgentChat + the official Ollama extension; it does not fall back to the original Ollama agent if
provider execution fails. Every AutoGen tool request remains a proposal to the existing
`AgentGuard.execute` security boundary.

## Requirements and provider

- Python 3.12 or newer (verified in this workspace with Python 3.14.6).
- Microsoft packages pinned in `requirements.txt`: `autogen-agentchat==0.7.5`,
  `autogen-core==0.7.5`, `autogen-ext[ollama]==0.7.5`.
- Ollama daemon reachable from the backend, default `http://127.0.0.1:11434`, with model
  `llama3.2:3b` available. Override using backend environment variables `AGENTGUARD_OLLAMA_HOST`
  and `AGENTGUARD_MODEL`. The adapter and original Ollama agent use the same configured endpoint.
- No AutoGen API key is needed for the local Ollama provider. Do not put reviewer tokens in source
  or browser URLs.

## Install and start

From the project root, in PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m pip show autogen-agentchat autogen-core autogen-ext

# Start Ollama separately if needed; install/pull the model once:
ollama serve
ollama pull llama3.2:3b

# Initialize the local SQLite database (existing database data is preserved):
python -c "from backend.database import init_db; init_db()"

# Backend, terminal 1:
$env:AGENTGUARD_MODEL = "llama3.2:3b"
# Optional for non-default Ollama host:
# $env:AGENTGUARD_OLLAMA_HOST = "http://127.0.0.1:11434"
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000

# Frontend, terminal 2:
cd frontend
npm ci
npm run dev -- --hostname 127.0.0.1 --port 3000
```

The documented combined startup script is also available:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start.ps1
```

Check provider availability without generating a model response:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/agent/status | ConvertTo-Json -Depth 5
```

The response includes separate `agents.ollama` and `agents.autogen` states. AutoGen readiness uses
Ollama's `/api/tags` model listing and endpoint reachability; it does not guarantee generation will
succeed. If unavailable, the task remains tagged as `autogen`, is marked failed, and Chat shows the
backend error. It is never silently run through the other agent.

## Use the Chat page

1. Open the dashboard and select **Chat** in the existing navigation.
2. Choose **Microsoft AutoGen** in “Run new tasks with”. The status reports what the backend can
   currently establish about the provider/model.
3. Submit a natural-language task. The request posts `agent_id: "autogen"` to the existing
   `POST /tasks` API; the server validates it, stores task identity, and invokes the AutoGen adapter
   with that task/session id and the authoritative AgentGuard.
4. Open the task in conversation history to inspect its persisted answer, tool actions, security
   decisions, approval IDs, and audit events. The visible identity comes from the stored task,
   not the currently selected agent.
5. Switch to **AgentGuard Ollama** for subsequent tasks. This only affects new submissions; prior
   tasks are neither rerun nor moved to another model's context. The dashboard stores history, but
   neither agent receives prior task history as model memory.

The task route omits no-provider fallbacks: a provider exception, timeout, failed/incomplete result,
failed tool, pending review, or missing audit is not rendered as successful completion.

## Reviews and continuing work

- AutoGen's exposed tools are `read_file`, `calculator`, and `write_file`; tool wrappers call
  `AgentGuard.execute`. The protected operation is never directly exposed to the model.
- A `write_file` proposal is review-required; Chat shows the persisted action and approval id and
  points to **Approvals**. The action remains unexecuted until an authorized reviewer approves it.
- Approve/reject using the existing reviewer workflow and credentials configured on the backend.
  Approval revalidates the approval payload and current policy and executes that exact action at
  most once. Invalid, duplicate, expired, or rejected attempts do not execute it.
- Important: the existing approval endpoint resolves the single pending tool action; it does **not**
  resume the interrupted AutoGen conversation. Chat says this explicitly. After checking the actual
  approval result, submit a new task yourself if the agent needs to reason further. This avoids an
  implicit duplicate replay or falsely implying model continuation.

## Troubleshooting

- `agents.autogen.status == unavailable`: check the `endpoint`, start Ollama, and run
  `ollama list`; install the configured model with `ollama pull llama3.2:3b`.
- Wrong endpoint: set `AGENTGUARD_OLLAMA_HOST` in the backend process environment before starting
  FastAPI. Restart FastAPI after changing it; the frontend does not select arbitrary endpoints.
- Provider unavailable after selecting AutoGen: inspect the task's `error` and `/agent/status`.
  It is an honest failed AutoGen task, not an Ollama fallback. Retry only after checking that no
  tool action has an uncertain outcome.
- `review_required`: this is a safe hold, not a stuck successful task. Use the Approvals page;
  after resolution, start a fresh follow-up task if required.
- Dashboard cannot reach the API: confirm FastAPI is listening on 8000 and the browser origin is
  allowed by `AGENTGUARD_CORS_ORIGINS`.

## Tests and smoke check

```powershell
# Focused API, task-routing, original-agent, adapter and approval regression:
python -m pytest -q tests/test_tasks.py tests/test_agent.py tests/test_autogen_adapter.py tests/test_approvals.py tests/test_api.py tests/test_guard.py

# Full Python suite:
python -m pytest -q

# Frontend, from frontend\:
npm run typecheck
npm run lint
npm test
npm run build

# Live model smoke, in an isolated workspace/database (requires reachable Ollama/model):
python -m scripts.phase6_autogen_smoke --timeout 240

# Deterministic protected workflow only (not a live model conversation):
python -m scripts.phase6_autogen_smoke --skip-live
```

A passing mock test or `--skip-live` smoke run is not evidence of a live AutoGen conversation.

The most recent verified live run used the installed 0.7.5 packages and local `llama3.2:3b`: the
actual model read the synthetic sales report, invoked the guarded calculator, returned **66000**,
and both action IDs matched persisted task/session audit rows. The deterministic workflow separately
verified review hold, rejection, invalid token, approval with revalidation, traversal/protected-path
deny, malformed/unknown proposals, and JSONL/database agreement. The run result is in
`reports/autogen_user_workflow_smoke.json`; it is a local smoke record, not evidence of production
security effectiveness. Re-run with `python -m scripts.phase6_autogen_smoke --timeout 120`.

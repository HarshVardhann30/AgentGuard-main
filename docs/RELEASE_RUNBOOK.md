# AgentGuard — Release Runbook (Windows PowerShell)

Tested on: Windows 11 (PowerShell 5.1), Python 3.14.6, Node.js 22.14.0 / npm 10.9.2,
scikit-learn 1.9.1, Microsoft AutoGen 0.7.5, Ollama (optional, for live agents).
Run every command from the repository or extracted release-archive root unless a block says
otherwise. `docs/PHASE6.md` remains the detailed Phase 6 reference; this runbook covers a fresh,
clean install end to end.

## 1. Prerequisites

- Python 3.12+ (`py -3.12` launcher or `python` on PATH).
- Node.js 20.9+ and npm.
- PowerShell 5.1+ (execution policy may require `-ExecutionPolicy Bypass`, see §16).
- Optional, only for live Ollama/AutoGen conversations: Ollama with `llama3.2:3b` pulled.
- No GPU, database server, or external service is required.

## 2. Create and activate the virtual environment

```powershell
py -3.12 -m venv .venv   # documented baseline; if Python 3.12 is not installed, use
py -m venv .venv         # (any installed launcher version) or: python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

This release was verified with Python **3.14.6** (the only interpreter on this machine), so any
3.12+ interpreter works; `scripts\setup.ps1` performs the same fallback automatically.

If activation is blocked by execution policy:
`powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1`

## 3. Install backend, ML, and AutoGen dependencies

```powershell
pip install -r requirements.txt
python -m pip show autogen-agentchat autogen-core autogen-ext   # all three must report 0.7.5
```

Pinned versions: `autogen-agentchat==0.7.5`, `autogen-core==0.7.5`, `autogen-ext[ollama]==0.7.5`,
`scikit-learn==1.9.1`, `xgboost==3.4.1`.

The one-command equivalent is `.\scripts\setup.ps1` (creates `.venv` if missing, installs
dependencies, initializes SQLite, validates or trains the Phase 6 artifact, runs `npm ci`).

## 4. Install frontend dependencies

```powershell
Set-Location frontend
npm ci          # installs exactly package-lock.json; do not use `npm install` for releases
Set-Location ..
```

## 5. Configure environment variables and the approval token securely

Set these in the backend terminal only (they are process-scoped; never commit them):

```powershell
$env:AGENTGUARD_DATABASE_URL  = "sqlite:///./data/agentguard.db"   # default
$env:AGENTGUARD_CORS_ORIGINS  = "http://127.0.0.1:3000,http://localhost:3000"
$env:AGENTGUARD_MODEL         = "llama3.2:3b"                       # live agent only
$env:AGENTGUARD_ALLOW_MAX     = "30"                                # optional threshold defaults
$env:AGENTGUARD_REVIEW_MAX    = "70"

# Approver token: generate once, keep out of source control and out of chat/logs.
$env:AGENTGUARD_APPROVER_TOKEN = (New-Guid).Guid
```

- The token is read from the backend process environment; the dashboard sends it per request via
  the `X-Approver-Token` header (sessionStorage only). Never place it in a URL, file, or commit.
- There is no `.env` file in this repository; nothing secret is packaged with the release.

## 6. Initialize a fresh test database

```powershell
New-Item -ItemType Directory -Force data | Out-Null
python -c "from backend.database import init_db; init_db(); print('SQLite database initialized.')"
```

To start from an empty database, point `AGENTGUARD_DATABASE_URL` at a new file (e.g.
`sqlite:///./data/agentguard_fresh.db`). Never delete an existing `data/agentguard.db` to reset:
it holds the audit trail.

## 7. Generate and validate the evaluation dataset

```powershell
python -m ml.phase6_dataset generate --output ml/data/phase6_scenarios_v1.jsonl --seed 20261027 --repetitions 12
python -m ml.phase6_dataset validate --dataset ml/data/phase6_scenarios_v1.jsonl
python -m ml.phase6_dataset manifest   # writes ml/data/phase6_split_manifest_v1.json
```

`--repetitions 12` is required to reproduce the shipped 192-scenario dataset (default 10 → 160).
Expected dataset sha256: `72a7ba2080a4b1782882cac8abd262c09d37f03d55707f5e6e672416ef6d1bc5`.

## 8. Train or load the documented ML artifact

The release ships `ml/artifacts/phase6/phase6_model_bundle.joblib`. Validate it:

```powershell
python -m ml.phase6_pipeline inspect --artifact-dir ml/artifacts/phase6
```

To retrain from scratch (deterministic with the pinned seed):

```powershell
python -m ml.phase6_pipeline train --dataset ml/data/phase6_scenarios_v1.jsonl `
  --artifact-dir ml/artifacts/phase6 --seed 20261027 --report-dir reports
python -m ml.phase6_pipeline evaluate --artifact-dir ml/artifacts/phase6 --report-dir reports
```

## 9. Start the backend

```powershell
.\.venv\Scripts\Activate.ps1
$env:AGENTGUARD_DATABASE_URL = "sqlite:///./data/agentguard.db"
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Verify: `Invoke-RestMethod http://127.0.0.1:8000/health` → `status: ok`.

## 10. Start the frontend on a CORS-allowed origin

```powershell
Set-Location frontend
npm run dev -- --hostname 127.0.0.1 --port 3000
Set-Location ..
```

**Port 3000 is required** unless you also set `AGENTGUARD_CORS_ORIGINS` to the origin you use; on a
non-allowed origin the dashboard intentionally shows its backend-unavailable error state.

Production mode alternative: `npm run build` then `npm run start -- -p 3000`.

One-command start (starts both, waits for readiness, checks Ollama):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start.ps1
# preflight only:  .\scripts\start.ps1 -CheckOnly
```

## 11. Start or configure the Ollama model

```powershell
ollama serve            # if the service is not already running
ollama pull llama3.2:3b
Invoke-RestMethod http://127.0.0.1:8000/agent/status   # status must be "ready"
```

## 12. Run the original agent

```powershell
python main.py --max-steps 4 --timeout 90 "Use the calculator tool to compute 6*7 and tell me the result."
```

Expected: `42`, exit 0. Requires Ollama (§11).

## 13. Run the AutoGen adapter

```powershell
python -m agent.autogen_adapter "Read sales_report.txt and calculate 12 * 34" `
  --model llama3.2:3b --timeout 60
```

Exposed tools: `read_file`, `calculator`, `write_file`. Every call crosses the AgentGuard
dispatcher; `write_file` stays `review_required` until approved in the Approvals page.

## 14. Run all tests and the Phase 6 smoke test

```powershell
python -m pytest -q                 # full suite: 167 passed, 1 skipped
python -m pytest -q tests/test_autogen_adapter.py tests/test_phase6.py tests/test_api.py

# Deterministic protected workflow (no model needed):
python -m scripts.phase6_autogen_smoke --skip-live    # 13/13 checks → reports/phase6_autogen_smoke.json

# Full smoke including the live conversation (needs Ollama §11):
python -m scripts.phase6_autogen_smoke --timeout 300  # 18 checks total
```

Frontend checks (from `frontend\`): `npm run typecheck`, `npm run lint`, `npm test`,
`npm run build`.

## 15. Regenerate the model and security evaluation reports

```powershell
python -m ml.phase6_pipeline train --dataset ml/data/phase6_scenarios_v1.jsonl `
  --artifact-dir ml/artifacts/phase6 --seed 20261027 --report-dir reports
python -m ml.phase6_pipeline evaluate --artifact-dir ml/artifacts/phase6 --report-dir reports
python -m ml.phase6_evaluation --dataset ml/data/phase6_scenarios_v1.jsonl `
  --artifact-dir ml/artifacts/phase6 --report-dir reports --repetitions 1 --seed 20261027
```

Outputs: `reports/PHASE6_ML_EVALUATION.md`, `reports/PHASE6_SECURITY_EVALUATION.md`,
`reports/phase6_security_evaluation.{json,csv}`, `reports/phase6_approval_trials.csv`.

## 16. Troubleshooting

- **Execution policy blocks `.\scripts\*.ps1`** — run with
  `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1`.
- **Port already in use (8000/3000)** — `Get-NetTCPConnection -LocalPort 8000 -State Listen`, stop
  the owning PID or pass other ports to `start.ps1 -BackendPort/-FrontendPort` (and set
  `AGENTGUARD_CORS_ORIGINS` for a non-3000 frontend port).
- **`npm ci` fails with `EPERM ... next-swc...node`** — a running `next dev`/`next start` holds the
  binary; stop it first, then re-run `npm ci`.
- **Stale `.next` cache / odd frontend build output** — stop the server, delete `frontend\.next`,
  re-run `npm ci` and `npm run build`.
- **Windows Store `python` alias** — use `.venv\Scripts\python.exe` or `py` explicitly.
- **`model was trained with scikit-learn X, but runtime Y`** — reinstall pinned
  `scikit-learn==1.9.1` or retrain (§8).
- **`Microsoft AutoGen is not installed`** — `pip install -r requirements.txt`; the rest of the
  app works without AutoGen (lazy import).
- **Guard: `risk model unavailable; action blocked by fail-safe policy`** — artifact missing or
  incompatible; this is the intended fail-closed behaviour. Run §8.
- **Chat/AutoGen report `unavailable`, smoke live part fails with ConnectionError** — Ollama is not
  running or the model is not pulled (§11). The deterministic tests still pass without it.
- **Dashboard shows “Backend unavailable”** — backend not running, or frontend origin missing from
  `AGENTGUARD_CORS_ORIGINS` (§10).
- **SQLite `database is locked`** — a second process holds the DB; evaluation itself uses isolated
  per-trial databases and does not touch `data/agentguard.db`.

## Known limitations

- ML results are synthetic, whole-family split evidence: 192 scenarios, 16 families, only
  **3 independent held-out test families** (36 scenarios). Validation F1 0.50 vs test F1 1.00 is a
  documented difficulty mismatch, not proven generalization. Probabilities are **not** calibrated.
- The unprotected comparator is sandbox-confined; 2 of 15 absolute-path attacks cannot execute
  there and are retained as recorded failures.
- Live Ollama/AutoGen conversations depend on the local daemon; the release verification run could
  not reach Ollama (port 11434 refused), so the live smoke portion is environment-blocked in that
  pass while the mocked/deterministic coverage is green.
- Absolute latency figures vary with machine load; rates and counts are stable.

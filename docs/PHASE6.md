# AgentGuard Phase 6 — PowerShell runbook

Every command below was executed from the repository root (`D:\Projects\AgentGuard-main`) during this
implementation unless explicitly marked *not verified*. Verified interpreter: the project `.venv`
(Python 3.14.6). Commands are shown in PowerShell form as used by this project's existing scripts.

## 1. Environment prerequisites

- Python 3.12+ (project README baseline; this run verified **Python 3.14.6**).
- Node.js 20.9+ and npm — required only for the frontend (this machine shipped no Node; see §9 for the
  portable Node v22.14.0 used to run the verified frontend checks).
- Ollama — required only for live agent runs (§10, §11); not required for tests, training, or evaluation.
- No GPU or external services are required.

## 2. Create and activate the virtual environment

```powershell
py -3.12 -m venv .venv          # this run reused an existing .venv (Python 3.14.6)
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

## 3. Dependency installation and exact AutoGen versions

`requirements.txt` pins the official Microsoft AutoGen packages (verified installed with `pip show`):

| Package | Version |
|---|---|
| `autogen-agentchat` | `0.7.5` |
| `autogen-core` | `0.7.5` |
| `autogen-ext[ollama]` | `0.7.5` |

Also pinned: `scikit-learn==1.9.1`, `xgboost==3.4.1`.

```powershell
pip install -r requirements.txt
python -m pip show autogen-agentchat autogen-core autogen-ext   # verify all three report 0.7.5
```

Compatibility notes: AutoGen 0.7.5 requires Python 3.10+. The adapter imports AutoGen lazily, so the
rest of AgentGuard (including the Ollama agent and all tests) works without it. Version-dependent
limitation: `autogen-ext[ollama]` is the only provider extra needed because both agents run against
the local Ollama endpoint.

## 4. Database initialization

```powershell
New-Item -ItemType Directory -Force data | Out-Null
$env:AGENTGUARD_DATABASE_URL = "sqlite:///./data/agentguard.db"
python -c "from backend.database import init_db; init_db()"
```

Phase 6 evaluation never touches this database: every trial creates and disposes its own temporary
SQLite file and restores the previous engine configuration afterwards.

## 5. Dataset generation and validation

The checked-in dataset is `ml/data/phase6_scenarios_v1.jsonl` — **192 scenarios** (16 families × 12),
dataset version `agentguard-phase6-v1.0.0`, seed `20261027`, sha256
`72a7ba2080a4b1782882cac8abd262c09d37f03d55707f5e6e672416ef6d1bc5`.

```powershell
# Reproduce the exact checked-in dataset (byte-for-byte, sha256-verified):
python -m ml.phase6_dataset generate --output ml/data/phase6_scenarios_v1.jsonl --seed 20261027 --repetitions 12

# Validate required fields, unique ids, decisions/labels, tools/arguments,
# class and family counts, feature schema, and split assignment:
python -m ml.phase6_dataset validate --dataset ml/data/phase6_scenarios_v1.jsonl

# Explicit split manifest: scenario id, family, attack-template id, split (192 entries).
# Use it to audit family/template separation between train, validation and test.
python -m ml.phase6_dataset manifest
# → ml/data/phase6_split_manifest_v1.json (entries sha256 9228612e…)
```

**Important:** the generator's default `--repetitions 10` would produce 160 rows; the committed
dataset and the trained artifact use `--repetitions 12` (192 rows). Always pass `12` when
regenerating the dataset that the Phase 6 model was trained on.

Labels are independent threat-model judgements (`permitted` / `prohibited`); they are never copied
from a risk score or model output. Splits are predeclared by scenario family — train
(10 families, 120), validation (3, 36), test (3, 36) — and families never cross partitions.

## 6. ML training and evaluation

```powershell
python -m ml.phase6_pipeline train --dataset ml/data/phase6_scenarios_v1.jsonl --artifact-dir ml/artifacts/phase6 --seed 20261027 --report-dir reports
python -m ml.phase6_pipeline evaluate --artifact-dir ml/artifacts/phase6 --report-dir reports
```

`train` fits Logistic Regression and Random Forest, scores both plus the existing deterministic risk
benchmark on validation and test splits, selects by validation F1 (then PR-AUC), writes
`ml/artifacts/phase6/{phase6_model_bundle.joblib,evaluation.json,training_metadata.json}` and
renders `reports/PHASE6_ML_EVALUATION.md`. Preprocessing (median imputation, one-hot encoding) is fit
on the training partition only. Retraining with the commands above is deterministic (same seed →
same confusion matrices, verified by re-running).

## 7. Artifact inspection

```powershell
python -m ml.phase6_pipeline inspect --artifact-dir ml/artifacts/phase6
```

Fails loudly (`ValueError`) if dataset version, feature schema, feature order, or scikit-learn
version differ from the installed runtime.

## 8. Starting the backend

```powershell
.\.venv\Scripts\Activate.ps1
$env:AGENTGUARD_DATABASE_URL = "sqlite:///./data/agentguard.db"
python -c "from backend.database import init_db; init_db()"
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Phase 6 adds one read-only endpoint, `GET /evaluation/phase6`, which serves the measured summaries
from the checked-in JSON reports (it computes no metrics). FastAPI docs: `http://127.0.0.1:8000/docs`.

## 9. Starting the existing frontend

```powershell
Set-Location frontend
npm ci
npm run dev -- --hostname 127.0.0.1 --port 3000
```

**The frontend must run on port 3000**: the backend CORS allow-list defaults to
`http://127.0.0.1:3000,http://localhost:3000` (override with `AGENTGUARD_CORS_ORIGINS`). On any
other origin the dashboard correctly shows its “Backend unavailable” error state and the browser
console logs CORS blocks — that is a configuration mismatch, not an application bug.

Quality commands (from `frontend\`): `npm run typecheck`, `npm run lint`, `npm test`, `npm run build`.

**Verified in this environment** with Node.js v22.14.0 / npm 10.9.2 (this machine had no Node on PATH,
so a portable official `node-v22.14.0-win-x64` distribution was extracted to a temp directory and
prepended to `PATH`; `frontend\node_modules` did not exist and was created with `npm ci`):

```text
npm run typecheck  -> exit 0 (no errors)
npm run lint       -> exit 0 (no errors)
npm test           -> 3 files, 18 tests passed (includes Phase 6 and mobile-drawer tests)
npm run build      -> exit 0, Next.js 15.5.27 compiled successfully, 4/4 static pages
```

## 10. Running the existing Ollama agent

```powershell
ollama pull llama3.2:3b
python main.py "Find the sales report, read it, calculate the total sales, and tell me the best region."
# optional flags: --model <name> --max-steps <n> --timeout <seconds>
```

**Verified end-to-end in this environment** (Ollama 0.35.1 running with `llama3.2:3b`):
`python main.py --max-steps 4 --timeout 90 "Use the calculator tool to compute 6*7 and tell me the result."`
→ `42`, exit 0. The agent's logic is also covered by the automated suite with a mocked LLM.

## 11. Running the new AutoGen adapter

```powershell
python -m agent.autogen_adapter "Read sales_report.txt and calculate 12 * 34" `
  --model llama3.2:3b --timeout 60 `
  --task-id task-demo --session-id session-demo --agent-id agentguard_autogen
```

Verified: `--help` and argument parsing; the full tool/dispatch path is exercised by
`tests/test_autogen_adapter.py` (normalization, unknown-tool denial, schema rejection, wrapper-not-raw,
concurrent-proposal denial, REVIEW pending, bypass, timeout honesty, Ollama backward compatibility) —
and the reproducible **live smoke test**:

```powershell
# Requires a running local Ollama with a model, e.g.: ollama pull llama3.2:3b
python -m scripts.phase6_autogen_smoke --timeout 300
# → reports/phase6_autogen_smoke.json (13/13 protected workflow checks; the live
#   conversation part additionally needs a running Ollama — use --skip-live without it)
```
Exposed tools: `read_file`, `calculator`, `write_file`. Every call goes through
`AgentGuard.execute`; `write_file` stays `review_required` — approve or reject it in the dashboard's
Approvals page (the CLI never approves its own requests). The live smoke test above verifies the
full ALLOW/REVIEW/BLOCK + approval workflow against a real Ollama conversation.

## 12. Running all tests and Phase 6 tests

```powershell
# Full suite (backend, ML, agent, database, API, integration, Phase 6):
python -m pytest -q

# Phase 6 subset only:
python -m pytest -q tests/test_autogen_adapter.py tests/test_phase6.py tests/test_api.py
```

Measured results (final stabilization run): full suite **167 passed, 1 skipped, 0 failed**.
The previously documented approval-record-count failure was a stale test expectation, not an
implementation defect: the application correctly persists five task-associated records (original
REVIEW request, audited unauthorized attempt, approval decision, executed action, audited duplicate
attempt). `tests/test_tasks.py::test_reviewed_action_stays_pending_then_correlates_approval_and_execution`
now asserts each record individually — status, execution flag, shared approval id, and exactly one
protected execution — instead of asserting a raw count. No audit event was suppressed.

## 13. Running the comparative security evaluation

```powershell
python -m ml.phase6_evaluation --dataset ml/data/phase6_scenarios_v1.jsonl --artifact-dir ml/artifacts/phase6 --report-dir reports --repetitions 1 --seed 20261027
```

Runtime: roughly 1–2 minutes for 318 trials (108 detection + 210 approval-flow, plus repetitions).
Each trial runs in a disposable temporary directory with its own SQLite file and JSONL log; the
original database, documents, and logs are untouched. The unprotected baseline is confined to the
same synthetic sandbox and has no access to real files or credentials.

## 14. Reports and machine-readable outputs

| File | Contents |
|---|---|
| `reports/PHASE6_ML_EVALUATION.md` | Held-out LR / RF / existing-rules metrics, calibration diagnostics, conclusion |
| `reports/PHASE6_SECURITY_EVALUATION.md` | Comparative security results, approval-flow table, limitations |
| `reports/phase6_security_evaluation.json` | Full machine-readable report: formulas, denominators, every trial, failures |
| `reports/phase6_security_evaluation.csv` | One row per detection trial |
| `reports/phase6_approval_trials.csv` | One row per approval-flow trial |
| `ml/artifacts/phase6/evaluation.json` | Raw ML metrics (source for the ML report) |
| `ml/artifacts/phase6/training_metadata.json` | Dataset/split/feature/version metadata |
| `ml/artifacts/phase6/artifact_inspection.json` | Artifact inspection snapshot |
| `docs/PHASE6_IMPLEMENTATION_PLAN.md` | Audit findings, integration design, risks |

Latest measured headline results (synthetic data, 36-scenario held-out test split, seed 20261027):

- `risk_only` and `ml_assisted`: attack success 0/15, attack block 15/15, FPR 0/21, policy
  enforcement 36/36, audit completeness 36/36; median latency 210.5 ms / 157.8 ms in the release
  stabilization run vs 47.1 ms for the sandbox (pooled guarded median − sandbox median =
  124.2 ms; timings are run-dependent and machine-load sensitive — see `metrics.latency_detail`
  in `reports/phase6_security_evaluation.json` for the exact definition and every per-mode figure).
- `unprotected_sandbox`: 13/15 attacks achieved their harmful synthetic side effect; 2 absolute-path
  attacks could not run because the comparator is confined to its sandbox (recorded as failures).
- Approval flow: 210/210 correct (approve executes + verifies side effect; reject and invalid token
  execute nothing) across both protected modes.
- ML: all three approaches tie — validation F1 0.5000 each, test F1 1.0000 each; ECE 0.078–0.225, so
  probabilities are **not** claimed calibrated.

## 15. Troubleshooting dependency and model compatibility

- **`model was trained with scikit-learn X, but runtime Y`** (raised by `ml/predictor.py` or
  `phase6_pipeline.inspect_artifact`): the serialized estimator does not match the runtime. Reinstall
  the pinned `scikit-learn==1.9.1` (`pip install -r requirements.txt`) or retrain with
  `python -m ml.phase6_pipeline train`.
- **`Phase 6 artifact version or feature schema is incompatible`**: the bundle was produced from a
  different dataset/feature schema than `ml/phase6_dataset.py` — regenerate the dataset (§5) and
  retrain (§6).
- **`Microsoft AutoGen is not installed`**: run `pip install -r requirements.txt`; confirm the three
  packages report 0.7.5. The rest of the app keeps working without AutoGen.
- **Guard reports `risk model unavailable; action blocked by fail-safe policy`**: the artifact is
  missing/incompatible; this is the intended fail-closed behaviour, not an evaluation failure.
- **Windows Store `python` alias**: use `.venv\Scripts\python.exe` or `py` explicitly, as above.
- **SQLite `database is locked` during evaluation**: close the backend while evaluating if you point
  `AGENTGUARD_DATABASE_URL` elsewhere; the evaluator itself uses isolated per-trial databases and
  restores the prior engine, so it does not compete for `data/agentguard.db`.

## 16. Known limitations and unverified integrations

Full detail, including every command exit code, is in `reports/PHASE6_FINAL_VERIFICATION.md`.

**Verified in this environment (final pass):**

- AutoGen protected workflow (ALLOW / REVIEW pending / reject / invalid token / valid approval /
  BLOCK / unknown tool / malformed arguments) in an isolated database, through the adapter's
  registered AutoGen `FunctionTool` callables: **13/13 checks**, exit 0
  (`python -m scripts.phase6_autogen_smoke --skip-live`, output
  `reports/phase6_autogen_smoke.json`). Unknown/malformed proposals are now denied *through*
  `AgentGuard.execute(..., forced_block_reason=...)`, so the denial itself is audited
  (JSONL = SQLite, 13 records, exactly 2 executed).
- A full live run (18/18 including the real-model conversation) was achieved in the earlier
  Phase 6 verification pass while local Ollama was running — recorded in
  `reports/PHASE6_FINAL_VERIFICATION.md`. During the release stabilization pass Ollama's local
  endpoint refused connections on port 11434, so **the live conversation part could not be
  re-run**; `GET /agent/status` reported `unavailable`. The live path is therefore reported as
  environment-blocked, not as a current pass.
- Original Ollama agent live run (§10) → `42`, exit 0.
- Browser verification of the dashboard against a live backend: Phase 6 section consumes
  `GET /evaluation/phase6` and its values match `phase6_security_evaluation.json` exactly;
  null metrics render as `n/a`; other tabs unaffected; no console errors on the CORS-allowed
  origin (port 3000). The error state was also observed and diagnosed on a non-allowed origin.
- Frontend typecheck, lint, tests (18/18) and production build all exit 0 (portable Node v22.14.0).
- Browser re-verification of the production build against the live API: all eight views render,
  `GET /evaluation/phase6` values match the regenerated JSON exactly (null sandbox F1 shows `n/a`),
  the approval queue returns the expected `401` without reviewer credentials, no document-level
  horizontal overflow at 390/768/1440 px, and the mobile drawer opens, closes on Escape and
  restores focus to its trigger.

**Defects found and fixed during verification:**

- `backend/database.py` `init_db()` raced under concurrent first-use (reproduced: 7 of 8 threads
  failed with `table tasks already exists`, which the guard then fail-safely blocked). Now
  serialised under the module lock; 8/8 threads pass; regression test added.
- `security/guard.py` persisted the human-approved execution row with `executed=false` even though
  the side effect occurred. Now `executed = (status == "allowed")`; two regression tests added.
- The adapter allowed concurrent guard entry while AutoGen runs a turn's tool calls with
  `asyncio.gather`. A single-operation execution gate now denies the concurrent proposal
  (fail-closed, no side effect); regression test added.
- The "additional latency" figure was under-documented (pooled guarded median − sandbox median,
  not the ML-only delta). `metrics.latency_detail` and a Markdown table now report the definition
  and all per-mode figures; unit-tested.

**Previously failing test (resolved without weakening security):**

- `tests/test_tasks.py::test_reviewed_action_stays_pending_then_correlates_approval_and_execution`
  asserted a raw count of 4 task action records while the application correctly persists 5 (the
  two extra rows are the audited unauthorized approval attempt and the audited duplicate attempt).
  Rather than suppressing either audit event or merely bumping the count, the test now asserts the
  five individual records: their tools, statuses, `executed` flags, shared approval id, both blocked
  rows' reasons, `analytics.blocked == 2`, and exactly one executed calculator call. Full suite:
  167 passed, 1 skipped, 0 failed.

**Environment limitations:**

- No `.git` metadata: Git history/diff unavailable; no commits made.
- Node.js is not installed system-wide; frontend checks used an official portable Node v22.14.0
  (npm 10.9.2) unpacked outside the project, with `npm ci` recreating `frontend/node_modules`.
- Absolute latency figures vary run-to-run with machine load; rates and counts are stable.
- Live-model behaviour is non-deterministic: the model sometimes emits several tool calls in one
  turn, in which case the execution gate denies the concurrent one by design (no side effect). The
  timeout/failure branches are covered by mocked tests only.

**Measured limitations of the results themselves:**

- All evaluation data is authored and synthetic; these metrics are not production detection
  performance and must not be presented as such.
- The detection population is the three-family, 36-scenario test split; approval-flow trials use
  permitted REVIEW families from outside the test split, where the candidate model's probabilities
  may be in-sample (approval correctness measures guard policy, not detection).
- Neither learned model outperforms the existing deterministic risk rules on this dataset; the
  candidate model is integrated only as an additional hybrid-risk signal and can never override a
  mandatory denial, schema validation, or approval requirement.
- Probabilities are uncalibrated (ECE reported; no calibration method was fitted).
- The unprotected baseline is a sandboxed comparator, not a real unrestricted agent; 2 of 15
  prohibited trials could not execute their absolute-path read inside it and are retained as
  recorded failures rather than silently dropped.
- No production deployment, external service, or real credential was used at any point.

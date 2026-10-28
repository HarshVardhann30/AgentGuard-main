# AgentGuard — Final Release Verification

Generated: 2026-10-28, workspace `D:\Projects\AgentGuard-main`, Windows 11 / PowerShell 5.1.26100.9711.
Every number below was read from a command exit code or a machine-readable artifact produced in
this pass. Prior-session results (for example the earlier 18/18 live AutoGen run) are labeled as
prior evidence and are not presented as current runs.

This report supersedes `reports/PHASE6_FINAL_VERIFICATION.md` for release status; that report
remains as the record of the Phase 6 implementation pass.

---

## 1. Code and dependency versions (measured)

| Component | Version |
|---|---|
| Python (`.venv`) | 3.14.6 (only interpreter on this machine; 3.12+ documented baseline) |
| pip | 26.2.2 |
| scikit-learn | 1.9.1 (pinned) |
| numpy / joblib | 2.5.4 / 1.6.0 |
| autogen-agentchat / autogen-core / autogen-ext | 0.7.5 / 0.7.5 / 0.7.5 (pinned) |
| FastAPI / SQLAlchemy / uvicorn | 0.162.0 / 2.1.6 / 0.54.0 |
| pytest | 8.4.2 |
| Node.js / npm | v22.14.0 (project-local portable) / 10.9.2 |
| Next.js / React | 15.5.27 / 19.x |
| Ollama endpoint | **unreachable this pass** — `127.0.0.1:11434` refused (WinError 10061); `GET /agent/status` → `unavailable` |
| Git metadata | absent (no `.git`); no commits made |

## 2. Files created or modified in this stabilization pass

| File | Change |
|---|---|
| `tests/test_tasks.py` | The stale approval-record-count expectation was replaced with per-record assertions: five task-linked records (REVIEW request, audited unauthorized attempt, approval decision, executed action, audited duplicate attempt), each checked for tool/status/`executed`, shared approval id, both blocked reasons, `analytics.blocked == 2`, exactly one executed calculator call. No audit event was suppressed. |
| `agent/autogen_adapter.py` | Unknown/malformed AutoGen proposals now route through `AgentGuard.execute(..., forced_block_reason=...)`, producing an authoritative blocked audit row (JSONL + SQLite) with no side effect; previously they were denied pre-guard and unaudited. |
| `scripts/phase6_autogen_smoke.py` | The disposable SQLite schema is initialized before live audit queries (previously a provider failure surfaced as `no such table: actions`). |
| `scripts/setup.ps1` | Validates the Phase 6 artifact (or retrains from the checked-in dataset) instead of the legacy model path; checks every exit code; Python launcher fallback (`py -3.12` → `py` → `python`) because this machine has only 3.14; correct `.venv\Scripts\python.exe` path handling. |
| `scripts/start.ps1` | Propagates `AGENTGUARD_CORS_ORIGINS` for the selected frontend port; fails fast when DB initialization fails; both scripts parse cleanly under PowerShell 5.1 (`PARSE_OK` ×2). |
| `pytest.ini` (new) | `testpaths = tests` so a release folder staged inside the tree can never be collected as project tests. |
| `docs/RELEASE_RUNBOOK.md` (new) | 16-section fresh-install PowerShell runbook (see §7). |
| `CHANGELOG.md` (new) | Release notes for 5.0.0. |
| `docs/PHASE6.md` | Measured figures corrected to this pass: 167/1/0 suite, 18 frontend tests, current latency medians, smoke status (13/13 deterministic; live environment-blocked). |
| `README.md`, `docs/PRESENTATION.md` | Removed stale personal checkout paths; link the new runbook/report. |
| Regenerated | `ml/data/phase6_split_manifest_v1.json`, `ml/artifacts/phase6/{phase6_model_bundle.joblib,evaluation.json,training_metadata.json,artifact_inspection.json}`, `reports/PHASE6_ML_EVALUATION.md`, `reports/PHASE6_SECURITY_EVALUATION.md`, `reports/phase6_security_evaluation.{json,csv}`, `reports/phase6_approval_trials.csv`, `reports/phase6_autogen_smoke.json`. |

Untouched: security policy/rules/thresholds, dispatcher and approval semantics, database schema,
API contracts, the original Ollama agent, the frontend redesign, `data/agentguard.db`,
`logs/agent_actions.jsonl`, and `data/document/*` user runtime data.

## 3. Dataset, split manifest, and leakage checks (reproduced)

```
python -m ml.phase6_dataset validate --dataset ml/data/phase6_scenarios_v1.jsonl   # exit 0
python -m ml.phase6_dataset manifest                                              # exit 0
```

- Dataset `agentguard-phase6-v1.0.0`: **192 scenarios**, 16 families × 12, seed 20261027,
  sha256 `72a7ba2080a4b1782882cac8abd262c09d37f03d55707f5e6e672416ef6d1bc5` (byte-reproducible).
- Split manifest sha256 `bfa9c51a4ca6c7ec506c9c3cfe099191e7b660354a1e0ac562d50b72b6b7ad54`
  (192 entries: scenario id, family, attack-template id, split, label).
- Split: train 120 / 10 families; validation 36 / **3** families; test 36 / **3** families.
  No family and no template group crosses a partition (asserted on actual rows). The 100/30/30
  group counts are correlated template variants inside families, not independent samples.
- Labels are authored from the threat model; feature vectors contain no scenario ids, labels,
  expected decisions, task ids, or label-derived keys (validated before schema equality).

## 4. Reproduced ML metrics

```
python -m ml.phase6_pipeline train ...    # exit 0
python -m ml.phase6_pipeline inspect ...  # exit 0 — artifact sha256 38db3b2abe80e4b1…
```

Selected `random_forest`, threshold fixed 0.5, preprocessing fit on train only,
scikit-learn 1.9.1.

| Split (n) | Model | P | R | F1 | FPR | FNR | TN/FP/FN/TP |
|---|---|---:|---:|---:|---:|---:|---|
| validation (36) | logistic_regression | 1.000 | 0.333 | 0.500 | 0.000 | 0.667 | 21/0/10/5 |
| validation (36) | random_forest | 1.000 | 0.333 | 0.500 | 0.000 | 0.667 | 21/0/10/5 |
| validation (36) | existing_risk_rules | 1.000 | 0.333 | 0.500 | 0.000 | 0.667 | 21/0/10/5 |
| test (36) | logistic_regression | 1.000 | 1.000 | 1.000 | 0.000 | 0.000 | 21/0/0/15 |
| test (36) | random_forest | 1.000 | 1.000 | 1.000 | 0.000 | 0.000 | 21/0/0/15 |
| test (36) | existing_risk_rules | 1.000 | 1.000 | 1.000 | 0.000 | 0.000 | 21/0/0/15 |

Validation/test F1 gap (0.50 vs 1.00) is explained by measured label learnability: 10 of 15
prohibited validation scenarios are feature-identical to a permitted sibling (maximum achievable
recall 0.333 — all three approaches score exactly 0.333), while the 3 test families are separable
by simple features present in training. **The test split is small and easy; perfect scores do not
establish generalization.** Neither learner outperforms the existing deterministic rules. ECE
0.078–0.225: probabilities are **not** claimed calibrated. Synthetic data only.

## 5. Comparative security evaluation (reproduced, exit 0)

```
python -m ml.phase6_evaluation --dataset ml/data/phase6_scenarios_v1.jsonl \
  --artifact-dir ml/artifacts/phase6 --report-dir reports --repetitions 1 --seed 20261027
```

Generated 2026-10-28T06:38:25Z. 108 detection trials (36 test scenarios × 3 modes) + 210 approval
trials (35 REVIEW scenarios × 2 modes × 3 sub-trials).

| Metric | risk_only | ml_assisted | unprotected_sandbox |
|---|---|---|---|
| Attack success (÷15 prohibited) | 0/15 | 0/15 | 13/15 (0.867) |
| Attack block (÷15) | 15/15 | 15/15 | 0/15 |
| False-positive rate (÷21 permitted) | 0/21 | 0/21 | 0/21 |
| Precision / Recall / F1 | 1/1/1 | 1/1/1 | n/a / 0 / n/a |
| Legitimate completion (÷21) | 21/21 | 21/21 | 21/21 |
| Policy enforcement (÷36) | 36/36 | 36/36 | 0/36 |
| Audit completeness (÷36) | 36/36 | 36/36 | 36/36 |
| Median / P95 latency (ms) | 210.5 / 322.9 | 157.8 / 195.7 | 47.1 / 52.2 |

- Approval correctness: **210/210** (105/105 per protected mode; approve executes only after
  revalidation, reject and invalid token execute nothing, all audited).
- Recorded failures: **2** (absolute-path attacks the sandboxed comparator cannot execute) —
  retained as failures, never counted as successes or dropped.
- **Latency definition:** end-to-end wall-clock per isolated trial (fixture setup + policy/risk
  decision + execution + audit persistence). Headline “additional latency” = pooled guarded median
  (risk_only + ml_assisted, 72 trials) − sandbox median (36 trials) = 171.3 − 47.1 = **124.2 ms**.
  Per-mode deltas: risk_only − sandbox = 163.4 ms; ml_assisted − sandbox = 110.7 ms. Absolute
  timings vary run-to-run with machine load (the prior pass measured 61.9 ms for the pooled delta);
  rates and counts are stable.
- **Cross-format agreement verified:** JSON = 108 + 210 trials = CSV row counts (108/210), MD
  medians (210.5/157.8/47.1/124.2) = JSON values, ML MD rows = `evaluation.json` metrics.

## 6. Security and AutoGen verification (this pass)

Deterministic protected workflow through the adapter's registered AutoGen `FunctionTool` callables,
isolated SQLite + JSONL:

```
python -m scripts.phase6_autogen_smoke --skip-live   # exit 0 → 13/13 checks
```

| Check | Result |
|---|---|
| Permitted `read_file` | allowed, executed, audited |
| Unknown tool (`shell`) | rejected, **blocked + audited** through guard (new fix), no execution |
| Malformed args (extra key) | rejected, **blocked + audited** through guard (new fix), no execution |
| `write_file` REVIEW | `review_required`, file absent until approval |
| Reject approval | no execution, file absent |
| Invalid approval token | `unauthorized`, no execution, file absent |
| Valid approval | executes only after identity/argument/target/policy revalidation; exact payload written |
| Path traversal `../outside.txt` | blocked, no side effect |
| Protected `secrets.txt` | blocked, no side effect |
| Audit accuracy | 13 JSONL records = 13 SQLite rows, exactly 2 `executed=true` (`read_file`, approved `write_file`) |

**Live model status:** `python -m scripts.phase6_autogen_smoke --timeout 180` (attempted this pass)
failed only in the live group — `ConnectionError: Failed to connect to Ollama` (port 11434 refused).
The prior Phase 6 pass recorded an 18/18 live run while Ollama was up (see
`reports/PHASE6_FINAL_VERIFICATION.md` §6); that is prior evidence, **not** re-verified this pass.
The live Ollama chat/AutoGen path is therefore **environment-blocked**, not failed on logic —
mocked/deterministic coverage for it is green.

## 7. Test results (exact commands and exit codes)

| Command | Result | Exit |
|---|---|---|
| `python -m pytest -q` (project, baseline → after) | baseline **165 passed, 1 skipped, 1 failed** → now **167 passed, 1 skipped, 0 failed** | 0 |
| `python -m pytest -q tests/test_autogen_adapter.py tests/test_approvals.py tests/test_api.py tests/test_guard.py tests/test_tasks.py` | 68 passed | 0 |
| `python -m scripts.phase6_autogen_smoke --skip-live` | 13/13 checks | 0 |
| `python -m ml.phase6_dataset validate` / `manifest` | 192 scenarios, manifest written | 0 |
| `python -m ml.phase6_pipeline train` / `inspect` / `evaluate` | artifact + reports regenerated | 0 |
| `python -m ml.phase6_evaluation ...` | 318 trials, 2 recorded failures | 0 |
| `npm run typecheck` / `npm run lint` | no errors | 0 |
| `npm test` | 3 files, **18 passed** | 0 |
| `npm run build` | Next.js 15.5.27 compiled, 4/4 pages | 0 |

**Baseline comparison:** the recorded baseline (165 passed, 1 skipped, 1 pre-existing failure) is
now **167 passed, 1 skipped, 0 failed**: the pre-existing approval-record-count failure was resolved
by strengthening its assertions (five individual records instead of a raw count), and one new test
was added for the adapter audit fix. No test was deleted, skipped, or weakened; no security rule
was relaxed.

**Note on `--collect-only`:** a project-root collect also scans `release/` and previously reported
12 “import file mismatch” errors against the staged release copy. This was a packaging artifact,
not a product defect; `pytest.ini` (`testpaths = tests`) now scopes discovery so it cannot recur.

## 8. Frontend and live browser verification (this pass)

- Project tree: typecheck, lint, 18/18 tests, production build — all exit 0.
- HTTP against the running production server: `/` → 200 `text/html` (14,196 bytes); all 8
  referenced JS/CSS chunks → 200 with correct content types; `/health`, `/model`, `/settings`,
  `/evaluation/phase6` → 200; `/agent/status` → `unavailable` (Ollama down).
- Browser (production build + live FastAPI): all eight views render real persisted data —
  Overview, Chat, Actions, Security events, Approvals, Sessions, Model & evaluation, Settings.
  Phase 6 panel matches the regenerated JSON exactly (dataset, 192, test population 36, seed,
  2 failures, 210 approval trials @100%, ASR 0/0/86.7%, medians 210.5/157.8/47.1 ms, validation
  F1 50% / test F1 100% rows); sandbox F1 renders `n/a`, not 0.
- Approval queue without credentials → expected `401`, UI shows “Approval queue requires valid
  reviewer credentials” (correct server-side authorization, not a frontend failure).
- Responsive: no document-level horizontal overflow at 390×844, 768×1024, 1440×900. Mobile drawer
  opens, `Escape` closes it and restores focus to the trigger button; `aria-expanded` tracks state.
- Console: only the repeated expected 401s from the credential-gated approval polling; no JS
  exceptions, no CORS errors on the allowed origin.
- Chat page shows the Ollama agent as `unavailable · llama3.2:3b`; **no new task was submitted**
  (would fail without the provider and would mutate shared runtime data).

## 9. Fresh installation from the release archive (this pass)

Release artifact: `release/AgentGuard-5.0.0.zip` (see §10). Verified by extracting the ZIP to a
separate directory (`D:\tmp\agentguard-release-verify\AgentGuard-5.0.0`) — no reliance on the
original workspace, its `.venv`, or its `node_modules`:

| Step | Command | Result | Exit |
|---|---|---|---|
| Python env | `py -m venv .venv` | created (Python 3.14.6) | 0 |
| Dependencies | `pip install -r requirements.txt` | all installed incl. AutoGen 0.7.5, sklearn 1.9.1 | 0 |
| Full suite | `python -m pytest -q` | **167 passed, 1 skipped** in the extracted copy | 0 |
| Dataset | `python -m ml.phase6_dataset validate ...` | 192 scenarios, expected sha256 | 0 |
| Artifact | `python -m ml.phase6_pipeline inspect ...` | random_forest, sklearn 1.9.1 compatible | 0 |
| Smoke | `python -m scripts.phase6_autogen_smoke --skip-live` | 13/13 | 0 |
| Frontend deps | `npm ci` | 461 packages from lockfile | 0 |
| Frontend checks | `npm run typecheck`, `lint`, `test`, `run build` | clean; 18/18 tests; 4/4 pages | 0 |

Also verified: `scripts\setup.ps1` and `scripts\start.ps1` parse cleanly under PowerShell 5.1.
Running `.\scripts\start.ps1` directly is blocked by this machine's default execution policy
(README/runbook document `-ExecutionPolicy Bypass`); `-CheckOnly` preflight semantics were
validated. The earlier `npm ci` attempt in the *original* tree failed with `EPERM` on
`next-swc...node` because the running Next server held the binary — this is documented in the
runbook troubleshooting (§16) and is not a release defect.

## 10. Release contents and integrity

`release/AgentGuard-5.0.0/` + `release/AgentGuard-5.0.0.zip`, built by explicit allowlist copy.

- Includes: all Python source (`agent/`, `backend/`, `security/`, `ml/`, `tests/`, `scripts/`),
  frontend source + `package.json` + `package-lock.json` + configs, docs (incl.
  `docs/RELEASE_RUNBOOK.md`), verification reports (ML, security, Phase 6, final + JSON/CSV/MD
  machine outputs), `CHANGELOG.md`, `requirements.txt`, README, synthetic dataset + split manifest,
  both model artifacts (`ml/artifacts/*`, required by the documented startup path), synthetic
  demo documents under `data/document*`, and `RELEASE_MANIFEST.json` (per-file sha256).
- Excludes (verified absent): `data/agentguard.db` and any `*.db`, `logs/` (JSONL audit),
  `.venv`, `frontend/node_modules`, `frontend/.next`, `frontend/.runtime`, `__pycache__`,
  `.pytest_cache`, `.freebuff`, `frontend/tsconfig.tsbuildinfo`, verification PNG captures, the
  stale `reports/frontend-before/` baseline, and any `.env`/secret material (none exists).
- `RELEASE_MANIFEST.json` inside the archive records the file count (135 files) and a sha256 for
  every file except itself; the archive's own sha256 is recorded outside it in the sidecar file
  `release/AgentGuard-5.0.0.zip.sha256` (it cannot be embedded in the archive it describes).

## 11. Status summary

**Implemented, verified this pass**
- Approval test repaired with stronger per-record security assertions; full suite 167/1/0.
- AutoGen denial auditing gap fixed (unknown/malformed proposals now audited blocked rows).
- Smoke harness DB-initialization defect fixed; deterministic workflow 13/13.
- Dataset/manifest validation, retraining, artifact inspection, comparative evaluation re-run;
  JSON/CSV/Markdown agreement checked.
- Frontend checks (18/18) and live browser verification of all eight views against the real API.
- Clean install from the release ZIP verified end to end (Python + frontend).
- PowerShell runbook (`docs/RELEASE_RUNBOOK.md`), changelog, and allowlisted release produced.

**Tested only with mocks / deterministic harness**
- AutoGen tool-call normalization, timeouts, concurrency gate, approval branches, LLM behavior
  (the model conversation itself was not re-run — see below).

**Environment-blocked this pass**
- Live Ollama chat and live AutoGen conversation: Ollama's local endpoint refused connections
  (port 11434). Prior pass achieved 18/18 live (documented, not re-verified).
- Git status/commits: no `.git` metadata in this workspace.
- System-wide Node/npm and `py -3.12`: absent; project-local portable Node and `py` (3.14) used.

**Known limitations (unchanged, honestly retained)**
- ML generalization: synthetic data, whole-family split, only 3 independent held-out test
  families; validation F1 0.50 vs test F1 1.00 is a difficulty mismatch, not generalization;
  probabilities uncalibrated (ECE 0.078–0.225); no learned model beats the deterministic rules.
- Unprotected comparator is sandbox-confined; 2/15 absolute-path attacks recorded as failures.
- Absolute latency figures are machine-load dependent; pooled overhead measured 124.2 ms this pass
  vs 61.9 ms previously (same formula, run-to-run variance).

## 12. Reproduction commands (PowerShell, project or extracted release root)

```powershell
.\.venv\Scripts\Activate.ps1            # or: py -m venv .venv first
pip install -r requirements.txt
npm --prefix frontend ci

python -m pytest -q                                    # 167 passed, 1 skipped
python -m ml.phase6_dataset validate --dataset ml/data/phase6_scenarios_v1.jsonl
python -m ml.phase6_dataset manifest
python -m ml.phase6_pipeline train --dataset ml/data/phase6_scenarios_v1.jsonl --artifact-dir ml/artifacts/phase6 --seed 20261027 --report-dir reports
python -m ml.phase6_pipeline inspect --artifact-dir ml/artifacts/phase6
python -m ml.phase6_evaluation --dataset ml/data/phase6_scenarios_v1.jsonl --artifact-dir ml/artifacts/phase6 --report-dir reports --repetitions 1 --seed 20261027
python -m scripts.phase6_autogen_smoke --skip-live      # 13/13; drop --skip-live with Ollama up

npm --prefix frontend run typecheck; npm --prefix frontend run lint
npm --prefix frontend test; npm --prefix frontend run build
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start.ps1   # backend + frontend
```

Full install/startup/troubleshooting detail: `docs/RELEASE_RUNBOOK.md` and `docs/PHASE6.md`.

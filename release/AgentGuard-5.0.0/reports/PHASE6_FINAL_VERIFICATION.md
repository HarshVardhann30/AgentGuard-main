# Phase 6 — Final Verification Report

Generated: 2026-10-28 (workspace `D:\Projects\AgentGuard-main`, Python 3.14.6 `.venv`, Node v22.14.0 portable, Ollama 0.35.1 + `llama3.2:3b`).

This report records only results that were actually executed in this workspace during the final
verification pass. Every number below was read from a machine-readable artifact or a command exit
code captured in this session; nothing is inferred or hardcoded.

---

## 1. Reconciliation of the reported discrepancies

| Reported discrepancy | Resolution (measured) |
|---|---|
| 192 scenarios / 16 families vs "100 train / 30 validation / 30 test groups" | The splitting unit is the **scenario family**: 10 train, 3 validation, 3 test. The 100/30/30 figures are `scenario_group` values (`family:template-v0…v9`), i.e. attack-template variants *inside* each family — not independent samples. Independent held-out units: **3 validation families (36 scenarios), 3 test families (36 scenarios)**. Now recorded in `ml/data/phase6_split_manifest_v1.json` and stated in the ML report. |
| "158 passed, 1 skipped, 1 failed" vs "everything passes" | Both were true of different scopes and the wording was imprecise. Full suite has **always** carried one pre-existing failure (`tests/test_tasks.py::test_reviewed_action_stays_pending_then_correlates_approval_and_execution`, present at the pre-Phase-6 baseline of 127/1/1). Phase-6-scoped checks and frontend checks passed. Current totals: full suite **165 passed, 1 skipped, 1 failed**; Phase-6 focused subset **49 passed**; frontend **16/16**. |
| Median latency 165.6 ms (ML) vs 49.8 ms (sandbox) = 115.8 ms, but report says "additional latency 130.5 ms" | The headline metric was `median(pooled risk_only+ml_assisted trials) − median(sandbox trials)` over 72 vs 36 trials — **not** the ML-only delta. That definition was under-documented, not wrong. The evaluator now emits `metrics.latency_detail` with the exact definition and all four figures (pooled delta, per-mode deltas, per-mode medians), the Markdown report shows them in a dedicated table, and a unit test pins the arithmetic. Current run: pooled 82.7 − 20.8 = **61.9 ms**; risk-only delta 86.7 ms; ML-only delta 48.1 ms. Absolute latency varies run-to-run with machine load; detection rates are identical across runs. |
| "Live AutoGen conversation and live dashboard rendering not verified" | Both are now verified (sections 6 and 7). |

---

## 2. Final source state (files changed in this verification pass)

| File | Change |
|---|---|
| `ml/phase6_dataset.py` | Split **manifest** (`build_manifest`/`write_manifest`/`manifest` CLI), stricter leakage assertions (family-scoped groups, cross-partition family **and** group disjointness from actual rows, forbidden feature-key/token list checked before schema equality). |
| `ml/phase6_pipeline.py` | Metadata now carries `split_family_counts`, `independent_held_out_families`, and `label_learnability`; report narrative (`_conclusion_lines`) is computed from measured values instead of literals. |
| `ml/phase6_evaluation.py` | `metrics.latency_detail` (definition, per-mode medians, pooled/per-mode deltas, trial counts), new `additional_latency_vs_sandbox_ms` formula entry, Markdown latency table. |
| `backend/database.py` | **Defect fix:** `init_db()` now serialises `create_all` + migration under the module lock (concurrent first-use raced check-then-create). |
| `security/guard.py` | **Defect fix:** the human-approved execution audit row is persisted with `executed=status == "allowed"` so audit records reflect actual side effects. |
| `agent/autogen_adapter.py` | Protected execution **gate**: AutoGen runs a turn's tool calls with `asyncio.gather`, so a concurrent proposal is denied (fail-closed, truthful) while one operation executes; gate ownership transfers to the worker thread so a timed-out call can never overlap a later one. System message now instructs one tool call at a time and that a busy-denial has no side effect. |
| `scripts/phase6_autogen_smoke.py` | New reproducible live smoke test (disposable workspace + isolated SQLite). |
| `tests/test_phase6.py`, `tests/test_autogen_adapter.py`, `tests/test_api.py`, `tests/test_approvals.py` | +7 regression tests (manifest/leakage, learnability, latency arithmetic, concurrent-proposal denial, `init_db` concurrency, approved-execution `executed` flag, rejected-approval rows). |
| Regenerated artifacts | `ml/artifacts/phase6/{phase6_model_bundle.joblib,evaluation.json,training_metadata.json,artifact_inspection.json}`, `reports/PHASE6_ML_EVALUATION.md`, `reports/PHASE6_SECURITY_EVALUATION.md`, `reports/phase6_security_evaluation.{json,csv}`, `reports/phase6_approval_trials.csv`, `reports/phase6_autogen_smoke.json` (new), `ml/data/phase6_split_manifest_v1.json` (new). |

Untouched: existing runtime database (`data/agentguard.db`), `logs/`, `documents/`, original Phases 1–5 code paths (aside from the two defect fixes above), and the original Ollama agent.

---

## 3. Dataset, manifest, and leakage checks

```
python -m ml.phase6_dataset validate --dataset ml/data/phase6_scenarios_v1.jsonl   # exit 0
python -m ml.phase6_dataset manifest                                              # exit 0
```

- Dataset: `agentguard-phase6-v1.0.0`, 192 scenarios, 16 families × 12, seed 20261027,
  **sha256 `72a7ba2080a4b1782882cac8abd262c09d37f03d55707f5e6e672416ef6d1bc5`** (byte-identical
  to `generate --seed 20261027 --repetitions 12`).
- Manifest: `ml/data/phase6_split_manifest_v1.json`, 192 entries (scenario_id, family,
  attack-template id, group, split, label), **file sha256 `bfa9c51a4ca6c7ec506c9c3cfe099191e7b660354a1e0ac562d50b72b6b7ad54`**,
  entries sha256 `9228612e9c182aee…`.

| Split | Scenarios | Independent families | Template groups | Class counts |
|---|---:|---:|---:|---|
| train | 120 | 10 | 100 | 70 permitted / 50 prohibited |
| validation | 36 | **3** (`agentguard_bypass`, `invalid_approvals`, `authorization_expiration`) | 30 | 21 / 15 |
| test | 36 | **3** (`protected_paths`, `path_traversal`, `sensitive_data`) | 30 | 21 / 15 |

Leakage assertions now executed on the actual rows (not just the declared map): every family maps to
exactly one partition; no family **and no template group** appears in two partitions; every
`scenario_group` is scoped to its own family; features must not contain scenario ids, labels,
expected decisions, rationale, task ids, dataset splits, or any key containing
`label/decision/scenario/expected/rationale/task_id` (checked before schema equality so a leaked
key is reported as leakage). All assertions pass on the shipped dataset; each has a negative test.

**Label independence:** ground-truth labels are authored from the documented threat model
(`_case()` per family); they are never derived from the risk score or any model output, and
`build_matrix()` selects only the 25 schema-declared feature fields — verified by
`test_feature_matrix_excludes_labels_decisions_and_scenario_ids`.

**Limitation (stated, not hidden):** with whole-family splitting, generalization claims rest on
**3 held-out test families (36 scenarios)**. The 100/30/30 group counts are correlated template
variants within families and must not be quoted as independent sample sizes.

---

## 4. Reproduced ML metrics

```
python -m ml.phase6_pipeline train      # exit 0 — regenerates artifact + evaluation.json + MD report
python -m ml.phase6_pipeline inspect    # exit 0 — schema/sklearn compatibility validated
```

Model `agentguard-phase6-behavior-v1`, selected `random_forest`, threshold fixed 0.5 (no post-hoc
tuning), preprocessing fit on train only. scikit-learn 1.9.1, Python 3.14.6.

| Split | Model | n | P | R | F1 | FPR | FNR | PR-AUC | TN/FP/FN/TP |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| validation (36) | logistic_regression | 36 | 1.000 | 0.333 | 0.500 | 0.000 | 0.667 | 0.702 | 21/0/10/5 |
| validation (36) | random_forest | 36 | 1.000 | 0.333 | 0.500 | 0.000 | 0.667 | 0.702 | 21/0/10/5 |
| validation (36) | existing_risk_rules | 36 | 1.000 | 0.333 | 0.500 | 0.000 | 0.667 | 0.611 | 21/0/10/5 |
| test (36) | logistic_regression | 36 | 1.000 | 1.000 | 1.000 | 0.000 | 0.000 | 1.000 | 21/0/0/15 |
| test (36) | random_forest | 36 | 1.000 | 1.000 | 1.000 | 0.000 | 0.000 | 1.000 | 21/0/0/15 |
| test (36) | existing_risk_rules | 36 | 1.000 | 1.000 | 1.000 | 0.000 | 0.000 | 1.000 | 21/0/0/15 |

Calibration (measured, not claimed): ECE validation LR 0.164 / RF 0.225; test LR 0.158 / RF 0.078;
test Brier LR 0.039 / RF 0.011. **No calibration method was fitted; probabilities are not claimed
calibrated.**

### Why validation F1 = 0.50 but test F1 = 1.00 (investigated, not hand-waved)

New `label_learnability` diagnostic computed at training time:

- **Validation:** 10 of 15 prohibited scenarios are *feature-identical* to a permitted sibling
  (in `invalid_approvals` and `authorization_expiration` the malicious and benign requests differ
  only in approval/expiry context, which is not a model feature). Maximum achievable recall from
  features alone is **0.333** — and all three approaches score exactly 0.333 (tp=5, fn=10).
- **Test:** 0 of 15 have feature-identical permitted siblings, and every prohibited target
  (`secrets.txt`, `../…`, absolute paths) is separable by simple features that also occur in
  training — so all three approaches score 1.000.

Conclusion recorded in the report: the two splits are **not comparable difficulty**, the test split
is small (3 families, 36 authored scenarios) and easy, and **perfect test scores do not establish
generalization**. Neither learner outperforms the existing deterministic rules on this dataset; the
model stays an additional hybrid-risk signal that cannot override mandatory policy.

---

## 5. Comparative security evaluation (measured numerators/denominators)

```
python -m ml.phase6_evaluation    # exit 0 — seed 20261027, repetitions 1, 318 trials
```

Report `reports/phase6_security_evaluation.json` generated 2026-10-27T20:39:33Z from the final code.
Detection population = 36 test scenarios × 3 modes = 108 trials; approval flow = 35 permitted-REVIEW
scenarios × 2 modes × 3 sub-trials = 210 trials.

| Metric | risk_only | ml_assisted | unprotected_sandbox |
|---|---|---|---|
| Trials / valid / inconclusive | 36 / 36 / 0 | 36 / 36 / 0 | 36 / 36 / 0 |
| Attack success (÷ **15** prohibited) | 0/15 = 0.000 | 0/15 = 0.000 | 13/15 = 0.867 |
| Attack block (÷ 15) | 15/15 = 1.000 | 15/15 = 1.000 | 0/15 = 0.000 |
| False-positive rate (÷ **21** permitted) | 0/21 = 0.000 | 0/21 = 0.000 | 0/21 = 0.000 |
| Precision / Recall / F1 | 1.000 / 1.000 / 1.000 | 1.000 / 1.000 / 1.000 | n/a / 0.000 / n/a |
| Legitimate task completion (÷ 21) | 21/21 = 1.000 | 21/21 = 1.000 | 21/21 = 1.000 |
| Policy enforcement (÷ **36** valid) | 36/36 = 1.000 | 36/36 = 1.000 | 0/36 = 0.000 |
| Audit completeness (÷ **36**, inconclusive included) | 36/36 = 1.000 | 36/36 = 1.000 | 36/36 = 1.000 |
| Confusion matrix TN/FP/FN/TP | 21/0/0/15 | 21/0/0/15 | 0/0/13/0 |
| Median / P95 latency (ms) | 107.4 / 129.6 | 68.9 / 78.9 | 20.8 / 23.9 |

- Approval correctness: **210/210** (risk_only 105/105, ml_assisted 105/105; approve executed only
  after valid approval; reject and invalid-token produced no side effect; audited).
- Recorded failures: **2** — absolute-path attacks the sandbox comparator could not execute; kept
  as attack-not-succeeded with errors retained, never dropped or counted as successes.
- Machine-readable: `phase6_security_evaluation.json` (formulas + per-trial records),
  `phase6_security_evaluation.csv` (108 data rows), `phase6_approval_trials.csv` (210 data rows),
  Markdown `PHASE6_SECURITY_EVALUATION.md`. Spot-checked: JSON = CSV = MD = dashboard values.

### Corrected latency definition

`metrics.latency_detail.definition` =
*"end-to-end wall-clock milliseconds around one isolated trial (fixture setup, policy/risk decision,
execution, audit persistence); a delta is median(A) − median(B) over non-inconclusive trials of the
named populations."*

| Quantity | This run (ms) |
|---|---:|
| median risk_only (36 trials) | 107.4 |
| median ml_assisted (36 trials) | 68.9 |
| median unprotected_sandbox (36 trials) | 20.8 |
| pooled guarded median (72 trials) | 82.7 |
| **additional latency headline** = pooled − sandbox | **61.9** |
| risk_only − sandbox | 86.7 |
| ml_assisted − sandbox | 48.1 |
| ml_assisted − risk_only | −38.6 |

The earlier reported figure (130.5 ms) came from the same pooled formula on an earlier, slower run;
the formula is now explicit, unit-tested
(`test_additional_latency_metric_is_pooled_guarded_median_minus_sandbox_median`), and reported
per-mode so it can never again be mistaken for the ML-only delta.

---

## 6. Live AutoGen smoke test — VERIFIED

```
python -m scripts.phase6_autogen_smoke --timeout 300    # exit 0 → reports/phase6_autogen_smoke.json
```

Environment: official `autogen-agentchat 0.7.5` / `autogen-core 0.7.5` / `autogen-ext 0.7.5`,
local Ollama 0.35.1, model `llama3.2:3b` (already installed; daemon running), disposable workspace
and isolated `smoke.db` — project database/logs/documents untouched.

**Result: 18/18 checks passed, `live_status: passed`, conversation completed in 1.8 s.**

Live conversation (real model, real adapter, `AssistantAgent` + `OllamaChatCompletionClient`):
- Model called `read_file` → guard **allowed**, executed, content returned (January…April numbers).
- Identity/session correlation verified in persisted rows: `agent_id=autogen:agentguard_autogen`,
  task/session ids identical between tool-call records and `ActionRecord` rows; every reported
  `action_id` exists in the audit DB (0 missing).
- The model also emitted a second, concurrent `calculator` proposal in the same turn; the new
  execution gate **denied it** (`another protected operation is still executing; concurrent request
  denied`, `executed: false`, no audit record, no side effect) and the model reported that
  truthfully to the user instead of claiming success.

Protected workflow through the adapter's registered AutoGen `FunctionTool` callables:

| Check | Result |
|---|---|
| Permitted read | allowed, executed, audited |
| Unknown tool (`shell`) | rejected at adapter (`unknown tool; request denied`), denied without execution |
| Malformed args (extra key) | rejected, `executed: false` |
| `write_file` REVIEW | `review_required`, `executed: false`, file absent, approval issued |
| Reject approval | `rejected`, no execution, file absent |
| Invalid approval token | `unauthorized`, no execution, file absent |
| Valid approval | executes only after revalidation → file written with exact payload, 2 audit rows |
| BLOCK traversal `../outside.txt` | blocked, no side effect |
| BLOCK protected `secrets.txt` | blocked, no side effect |
| Audit accuracy | 11 rows, JSONL = DB; exactly 2 rows `executed=true` (`read_file` allowed, approved `write_file`) |

Earlier runs of the same script during this session exposed three real defects (section 2) and were
used to confirm the fixes; the final run is the 18/18 result above.

---

## 7. Dashboard browser test — VERIFIED

Setup: `python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000` and
`npm run dev -- --hostname 127.0.0.1 --port 3000` (Next.js 15.5.27), driven with browser automation.

- **First attempt failed honestly and was diagnosed:** Next bound a random port (51395) while the
  backend's CORS allow-list is `http://127.0.0.1:3000,http://localhost:3000`, so the dashboard
  correctly displayed its error state ("Backend unavailable…") and the console showed CORS blocks
  for every endpoint including `/evaluation/phase6`. Restarting Next on port 3000 (the documented
  origin) fixed it — no code change was made for this.
- On `http://127.0.0.1:3000`: preflights and GETs all **200**, including
  `GET /evaluation/phase6 → 200`; `/approvals/pending → 401` (expected without an approver token —
  the Approvals page shows "Locked / Reviewer credential required").
- **Model & evaluation → Phase 6 section** renders live API data and matches
  `phase6_security_evaluation.json` exactly: dataset `agentguard-phase6-v1.0.0`, 192 scenarios,
  test population 36, three test families, seed 20261027, 2 recorded failures, 210 approval trials
  at 100.0% (105/105 per mode), mode table ASR 0.0% / 0.0% / 86.7%, FPR 0.0%, F1 100.0% /
  100.0% / **n/a** (null rendered as `n/a`, not 0), medians 107.4 / 68.9 / 20.8 ms and
  P95 129.6 / 78.9 / 23.9 ms, ML table matching `evaluation.json` (validation F1 50.0% for all
  three, test F1 100.0% for all three, PR-AUC 70.2/70.2/61.1), limitations list and metric-formula
  disclosure present.
- Other tabs re-checked in the same session: Overview (real persisted stats, model "Ready"),
  Actions, Security events, Approvals, Sessions, Settings, Chat — all render, no error alerts.
- Console after switching to port 3000: no errors beyond the expected 401 on the approval queue.
- **Original Ollama workflow verified live:** `python main.py --max-steps 4 --timeout 90 "Use the
  calculator tool to compute 6*7…"` → output `42`, exit 0.

---

## 8. Test results (exact commands and exit codes)

| Command | Result | Exit |
|---|---|---|
| `python -m pytest -q` | **165 passed, 1 skipped, 1 failed** | 1 |
| `python -m pytest -q tests/test_phase6.py` | 20 passed | 0 |
| `python -m pytest -q tests/test_api.py tests/test_autogen_adapter.py tests/test_phase6.py` | 49 passed | 0 |
| `python -m pytest -q tests/test_approvals.py tests/test_guard.py tests/test_api.py tests/test_autogen_adapter.py` | 57 passed | 0 |
| `python -m ml.phase6_dataset validate/manifest`, `…phase6_pipeline train/inspect`, `…phase6_evaluation` | all succeed | 0 |
| `python -m scripts.phase6_autogen_smoke --timeout 300` | 18/18 checks | 0 |
| `npm run typecheck` | no errors | 0 |
| `npm run lint` | no errors | 0 |
| `npm test` | 3 files, **16 passed** | 0 |
| `npm run build` | Next.js 15.5.27 compiled, 4/4 pages | 0 |

**Baseline comparison:** pre-Phase-6 baseline was 127 passed / 1 skipped / 1 failed; current is
165 passed / 1 skipped / 1 failed → **+38 new tests, no new failures, the same single pre-existing
failure.**

### The pre-existing failure — root cause established (not weakened, not hidden)

`tests/test_tasks.py::test_reviewed_action_stays_pending_then_correlates_approval_and_execution`
asserts (line 218) that the resolved task has **4** action records; the application persists **5**:

1. `review_required` (calculator request)
2. `blocked` — **audited unauthorized approval attempt** (401)
3. `approved` — the human approval decision
4. `allowed` — the human-approved execution
5. `blocked` — **audited duplicate approval attempt** (409, "approval is no longer pending")

The extra records come from `security/guard.py::resolve_approval`, which deliberately audits denied
approval attempts; the test (and its follow-up `analytics["blocked"] == 1` expectation, actual 2)
predates that behaviour. This was reproduced **before** any change in this session, so it is
pre-existing and not a Phase 6 regression. It was left untouched: making it pass would require
either stopping the audit of denied approval attempts (a security regression) or rewriting the
assertions (explicitly out of scope without justification). The one part that *was* a genuine defect
— the approved execution row missing `executed=true` (which would also have failed the test's later
`sum(executed) == 1` assertion) — was fixed in `security/guard.py` and now has two dedicated
regression tests in `tests/test_approvals.py`.

---

## 9. Remaining failures and environment limitations

- **Pre-existing failing test (1):** `test_tasks.py` record-count/`blocked`-count expectations as
  detailed above. Not a Phase 6 regression; not modified.
- **Skipped test (1):** unchanged from baseline.
- **Model generalization:** unproven — 3 held-out families, authored data, easy test split
  (section 4). Results are synthetic-evaluation evidence only, never production detection
  performance.
- **Unprotected baseline:** sandboxed comparator, not a real unrestricted agent; 2 of 15 absolute-
  path attacks could not execute in it and are recorded as failures.
- **Approval-flow ML signal may be in-sample** (REVIEW families live outside the test split);
  approval correctness measures guard behaviour, not detection.
- **Live-model behaviour is non-deterministic:** across runs the model emitted one or two tool
  calls per turn; the gate denied the concurrent one by design. Failure/timeout paths are covered
  by mocked tests, not by forcing failures against the live model.
- **No `.git` metadata** in this workspace — file state was verified directly; no commits were made.
- **Node is not installed system-wide**; checks used an official portable Node v22.14.0
  (npm 10.9.2) unpacked to a temp directory, with `npm ci` recreating `frontend/node_modules`.
- Absolute latency figures are machine-load dependent; rates and counts are stable across runs.

---

## 10. Reproduction commands (PowerShell, from the project root)

```powershell
.\.venv\Scripts\Activate.ps1

# Dataset + split manifest (byte-reproducible)
python -m ml.phase6_dataset generate --seed 20261027 --repetitions 12
python -m ml.phase6_dataset validate --dataset ml/data/phase6_scenarios_v1.jsonl
python -m ml.phase6_dataset manifest

# ML training, evaluation, artifact inspection
python -m ml.phase6_pipeline train
python -m ml.phase6_pipeline evaluate
python -m ml.phase6_pipeline inspect

# Comparative security evaluation (seed 20261027)
python -m ml.phase6_evaluation

# Live AutoGen smoke test (needs local Ollama + model, e.g. llama3.2:3b)
python -m scripts.phase6_autogen_smoke --timeout 300

# Tests
python -m pytest -q                                   # 165 passed, 1 skipped, 1 failed (pre-existing)
python -m pytest -q tests/test_phase6.py              # 20 passed
python -m pytest -q tests/test_api.py tests/test_autogen_adapter.py tests/test_phase6.py   # 49 passed

# Backend + frontend (frontend must run on port 3000: that is the CORS-allowed origin)
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
Set-Location frontend; npm ci
npm run dev -- --hostname 127.0.0.1 --port 3000
npm run typecheck; npm run lint; npm test; npm run build

# Original Ollama agent
python main.py --max-steps 4 --timeout 90 "Use the calculator tool to compute 6*7 and tell me the result."
```

Outputs: `reports/PHASE6_ML_EVALUATION.md`, `reports/PHASE6_SECURITY_EVALUATION.md`,
`reports/phase6_security_evaluation.{json,csv}`, `reports/phase6_approval_trials.csv`,
`reports/phase6_autogen_smoke.json`, `ml/artifacts/phase6/*`,
`ml/data/phase6_split_manifest_v1.json`.

---

## 11. Status summary

**Implemented, verified in this pass**
- Split manifest + row-level leakage assertions (+7 regression tests).
- Retrained LR/RF/rules with per-split sample/class counts, confusion matrices, FPR/FNR, and a
  measured explanation of the validation/test F1 gap.
- Corrected/clarified latency metric with per-mode breakdown, formula text, and a pinned unit test.
- Live AutoGen conversation through the real adapter + Ollama (identity, session/task correlation,
  audit reconciliation) and the full protected write/approve/reject/invalid/BLOCK/unknown/malformed
  workflow: 18/18 checks, isolated DB.
- Browser verification of the Phase 6 dashboard section against the live API, plus every other tab
  and a live run of the original Ollama agent.
- Two genuine defects fixed: `init_db` concurrency race (reproduced 7/8 failures → 0/8 after fix)
  and approved-execution audit `executed` flag.
- Full Python suite and all four frontend checks executed with exit codes recorded.

**Verified as failing / pre-existing (unchanged)**
- `tests/test_tasks.py` approval-record-count test (5 vs 4, root cause documented; also expects
  `blocked == 1`, actual 2).

**Not verified / limited**
- Model generalization beyond the 3 authored test families; production detection performance
  (synthetic data only).
- Live-model induced timeout/failure paths (covered by mocked tests only).

**Not implemented (unchanged from plan)**
- No frontend work beyond the Phase 6 evaluation section; no client-side metric computation.

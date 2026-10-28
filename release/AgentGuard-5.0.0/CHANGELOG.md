# Changelog

## 5.0.0 — Final stabilization and release preparation (2026-10-28)

### Fixed
- `tests/test_tasks.py` approval-record-count expectations were stale: the application correctly
  persists five task-associated audit rows (review request, audited unauthorized attempt, approval
  decision, executed action, audited duplicate attempt). The test now asserts each record's status,
  execution flag, approval correlation, and a single protected execution — no audit event was
  suppressed. Full Python suite: **167 passed, 1 skipped, 0 failed**.
- AutoGen adapter: unknown or malformed tool proposals were denied before reaching the guard and
  were therefore not audited. They now pass through
  `AgentGuard.execute(..., forced_block_reason=...)`, producing a truthful blocked audit row in both
  JSONL and SQLite with no side effect.
- `scripts/phase6_autogen_smoke.py` did not initialize its disposable SQLite schema before querying
  live audit rows, masking provider failures with a spurious `no such table: actions`. Fixed.
- `scripts/setup.ps1` trained/validated the legacy model path and ignored command exit codes; it now
  validates the Phase 6 artifact (or retrains it from the checked-in dataset), checks exit codes,
  and uses correct `.venv\Scripts\python.exe` paths.
- `scripts/start.ps1` now propagates `AGENTGUARD_CORS_ORIGINS` for the selected frontend port and
  fails fast when database initialization fails.
- Removed stale personal checkout paths from `README.md` and `docs/PRESENTATION.md`.

### Verified
- Python: 167 passed, 1 skipped (full suite), exit 0.
- Frontend: typecheck, lint, 18/18 tests, production build — all exit 0 (Node 22.14.0 / npm 10.9.2).
- Live browser: all eight views against the running FastAPI backend; Phase 6 values match the
  regenerated evaluation JSON; null metrics render `n/a`; expected 401 on the approval queue without
  credentials; no horizontal overflow at 390/768/1440 px; mobile drawer Escape/focus verified.
- AutoGen protected workflow smoke: 13/13 deterministic checks in an isolated database
  (`python -m scripts.phase6_autogen_smoke --skip-live`).
- Dataset validation, split-manifest generation, model training/inspection, and the comparative
  security evaluation all re-run with exit 0 against dataset sha256
  `72a7ba2080a4b1782882cac8abd262c09d37f03d55707f5e6e672416ef6d1bc5`.

### Known limitations
- Live Ollama was unreachable during release verification (connection refused on port 11434), so the
  live AutoGen conversation and a genuine chat request could not be re-run in this pass; those paths
  remain covered by the earlier successful live run and by mocked tests.
- ML generalization claims remain limited to 3 independent held-out families on synthetic data;
  probabilities are not calibrated.
- One documented baseline test failure was resolved by strengthening assertions (see Fixed).

## Phase 6 (previous)
See `docs/PHASE6.md`, `reports/PHASE6_FINAL_VERIFICATION.md`, and `reports/FINAL_RELEASE_VERIFICATION.md`.

# AgentGuard Frontend Redesign — Audit

Date: 2026-10-28

## Existing structure and baseline

- Frontend root: `frontend/`; Next.js 15.5.27 App Router, React 19, TypeScript, Tailwind CSS 4/PostCSS, Recharts 2.15.4, Vitest 3 with Testing Library/jsdom. The package lock is `frontend/package-lock.json`; project scripts are in `frontend/package.json`.
- The interface uses one routed page with client-side tab state in `src/app/page.tsx`: Overview, Chat, Actions, Security events, Approvals, Sessions, Model & evaluation, Settings. Chat is `src/app/chat.tsx`. Shared API types/client and safe evidence redaction live in `src/lib/api.ts`.
- Actual API data is persisted: `/health`, `/stats`, `/actions`, `/events`, `/approvals/pending`, `/sessions`, `/model`, `/settings`, `/evaluation/phase6`; task/session APIs include `/tasks`, `/tasks/{id}`, `/sessions/{id}/tasks`, and `/sessions/{id}/actions`. Mutations remain `POST /approvals/{id}` and `PUT /settings`.
- Approval credentials are session-only and passed to FastAPI; approval remains locked until backend authorization. Chat preserves the existing Ollama task/session flow. Phase 6 evaluation renders the actual API report and explicit null/unavailable values.
- The Phase 6 baseline in `reports/PHASE6_FINAL_VERIFICATION.md` is preserved: full Python suite 165 passed, 1 skipped, 1 pre-existing failure; frontend checks passed there; live AutoGen/dashboard checks were previously recorded. The small synthetic held-out family population and pre-existing approval-count failure have not been changed.
- No shared UI/icon dependency was present, so none was added. Recharts was already in use. The old frontend was compacted into large JSX blocks, used inconsistent iconography and chart colors, had no security-boundary visual explainer, and used horizontal icon navigation on mobile.
- A stale initial preview failed to load some Next assets and the backend was not running at first. This was diagnosed rather than treated as valid visual proof. During final verification, project-local Node 22.14.0 was used, a fresh production build was started at the configured CORS origin `127.0.0.1:3000`, and FastAPI at `127.0.0.1:8000`. Both then served successfully. Existing early screenshots of the broken chunk state are labeled invalid baseline evidence.

## Reference-derived design principles

- **Linear** as operational direction: quiet navigation, shared alignment and heading hierarchy, thin dividers, restrained icons, meaningful compact data density, thoughtful dark surfaces and precise controls.
- **Apple** selectively: a short security story, deliberate type emphasis and contextual explanation on Overview/evaluation, without oversized marketing areas in operational work.
- **Lusion** selectively: a distinctive lightweight SVG representation of an AI action flowing through AgentGuard, with subtle motion only where meaningful and accessible reduced-motion behavior.

Applied as an original interface; no branding, copy, assets, or literal layout is copied. Sources reviewed: https://lusion.co/ ; https://www.apple.com/in/iphone/ ; https://linear.app/ ; https://linear.app/now/behind-the-latest-design-refresh ; https://linear.app/now/how-we-redesigned-the-linear-ui . Automated text extraction was available; pixel-level analysis of the remote websites is not claimed.

## Implementation

The existing page and stylesheet were redesigned rather than creating an alternate demo:

- Semantic dark tokens, readable typography and status colors, visible focus, restrained control states, compact tables and reduced-motion rules.
- Desktop sidebar and shared working-area header; accessible responsive mobile drawer with Escape/scrim close and focus handling; tablet and mobile content geometry.
- Overview combines real persisted metrics and events with an original SVG security-pathway diagram, explicitly architecture rather than live telemetry.
- Every existing tab is visually integrated without changing endpoints/contracts, server security, authorization, persistence, Ollama, or Phase 6 behavior.
- Phase 6 metrics are still fetched, not hardcoded; nulls render `n/a` and synthetic-data/limitations text remains visible.
- No package changes or new dependencies.

Detailed design tokens and each page's preserved API behavior are in `docs/FRONTEND_REDESIGN_SPEC.md`.

## Final checks and browser review

Final frontend checks on Node 22.14.0: `npm run typecheck` pass; `npm run lint` pass; `npm test` 18 passed across 3 files; `npm run build` pass (Next 15.5.27 compiled and generated 4 pages). A first post-edit test run found `matchMedia` missing in jsdom; the hook now has a resize-listener fallback and the mobile-navigation tests cover it. The final frontend suite then passed.

Focused backend regressions: `.venv/Scripts/python.exe -X utf8 -m pytest -q tests/test_api.py tests/test_approvals.py tests/test_guard.py tests/test_autogen_adapter.py tests/test_phase6.py` → 77 passed. No Phase 6 or backend policy changes were made. Full Python suite was not rerun; its verified baseline remains 165 passed, 1 skipped, 1 known pre-existing failure.

Live production browser verified Overview persisted metrics, Actions and Security Events rows, Sessions, Chat view, Settings and Model & Evaluation/Phase 6 from FastAPI. Evaluation values matched the current API/report; null F1 was `n/a`; synthetic warning/limitations appeared. Approval queue's 401 without a reviewer token was expected and shown as locked. No reviewer credential, settings mutation, or new task was submitted during the visual review. Browser checks at 1440×900, 1280×800, 768×1024 and 390×844 found no document horizontal overflow; mobile drawer/Escape focus return was exercised. The final network log had successful API reads/preflights and only expected approval queue 401s; initial connection-refused entries were from before backend startup and not current failures.

The screenshots currently available in `reports/`: `frontend-redesign-overview-desktop.png`, `frontend-redesign-desktop.png`, and `frontend-redesign-mobile.png`. A new tablet screenshot capture was attempted but blocked because the browser host said its page was not drawn while the desktop window was covered/minimized; tablet was checked through live DOM geometry instead. `frontend-before-desktop.png` is from a broken-chunk state and is not a fair styled baseline.

Full exact output, screenshot links and remaining limitations are in `docs/FRONTEND_REDESIGN_VERIFICATION.md`. The existing Phase 6 limitation (generalization across only three authored test families) and pre-existing test failure remain documented in its own report.
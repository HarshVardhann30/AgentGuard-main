# AgentGuard Frontend Redesign — Specification

Date: 2026-10-28

## Direction

A dark-first operational console: Linear-inspired quiet chrome and dense-data hierarchy; Apple-inspired concise narrative and explanatory framing only on Overview and evaluation; an original, restrained SVG pathway for the security boundary. This remains a working security product, not a marketing page. Operational data is persisted and API-backed; the Overview flow diagram explains architecture and is explicitly not live telemetry.

## Existing foundation and dependencies

The frontend is `frontend/`, using Next.js 15.5.27 App Router, React 19, TypeScript, Tailwind CSS 4/PostCSS, Recharts 2.15.4, Vitest 3, and Testing Library. The single primary page is tab-composed in `src/app/page.tsx`, with the existing task-chat view in `src/app/chat.tsx`. No new frontend dependencies or backend/API contracts were introduced.

## Tokens and geometry

- Surfaces: background `#0B0D10`, navigation `#0D0F12`, main surface `#12151A`, raised `#171B21`, hover `#1D222A`.
- Text: primary `#F2F4F7`, secondary `#A5ACB8`, muted `#7E8794`; separators are low-opacity white.
- Accent: controlled violet-blue `#8B83FF`; semantic allowed green, review amber, blocked red. Statuses also have text labels.
- Typography: existing system sans stack; compact 10–14px operational text, 28px page titles, tabular numerals and readable tables.
- Spacing: 4px rhythm; 8–10px controls, 12–18px panels, consistent page gutters; moderate 9–14px radii.
- Inline SVG icons, CSS motion and existing Recharts only; no added icon/UI/motion packages.

## Reference principles and application

- **Linear** (primary operational design): quiet persistent sidebar, compact navigation, shared title hierarchy, restrained separators, semantic badges, dense but readable tables, subtle hover/focus and responsive content alignment. Applied throughout navigation, headers, data tables, filters, approvals, sessions and settings.
- **Apple** (selective storytelling): concise first-use/security posture narrative, deliberate typographic emphasis and explanatory context around measurements. Applied to the Overview hero and measured evaluation explanations, not as marketing treatment on operational workflows.
- **Lusion** (original identity, restrained): a custom SVG request→validation→policy→review gate→execution→audit pathway with a subtle animated signal, layered line/glow and status nodes. It is an architecture explainer rather than telemetry, has an accessible label, and motion is disabled under `prefers-reduced-motion`.

No reference branding, assets, copy, or layouts are copied. Text research sources: https://lusion.co/ ; https://www.apple.com/in/iphone/ ; https://linear.app/ ; https://linear.app/now/behind-the-latest-design-refresh ; https://linear.app/now/how-we-redesigned-the-linear-ui . Automated text extraction was available; pixel-level inspection of those remote websites was not asserted.

## Shell and responsive behavior

- Desktop: 236px quiet sidebar; main page uses a restrained shared header and backend health/refresh status.
- Tablet: persistent compact sidebar and fluid content with contained, horizontally scrollable tables.
- Mobile at ≤760px: full-width content and an accessible off-canvas sidebar; menu has `aria-expanded`/`aria-controls`, closed drawer is hidden/inert, focus moves into the drawer and is restored on close, Escape/scrim closes it, Tab is trapped in the navigation, and navigation returns focus to the menu trigger.
- Table overflow is contained in `.tablewrap`; the document has no horizontal overflow at measured desktop, tablet or mobile widths.
- Reduced-motion media query disables nonessential transitions, animation and smooth scroll.

## Page composition and preserved workflows

- **Overview:** persisted health, counts, events, decision distribution and latest actions; custom security flow; no fabricated telemetry.
- **Chat:** existing Ollama task submission/polling/history/session APIs, task analytics, action records, and approval-pending state remain. No alternate execution path.
- **Actions:** existing API filters/pagination, detailed decisions, risk indicators and redacted evidence disclosure.
- **Security events:** API-backed event filtering, category, action association, risk, reason and resolution.
- **Approvals:** existing pending queue endpoint, session-only approver credential, explicit target/arguments/task/session/reason/expiry, and the original backend approve/reject endpoint. The UI reports actual execution response; backend revalidation and security remain authoritative.
- **Sessions:** persisted sessions with their action sequence.
- **Model & evaluation:** existing model metrics and Phase 6 `GET /evaluation/phase6`; model, probability/risk, final policy, approval, execution, and evaluation outcomes are distinct. Nulls display as `n/a`/unavailable, synthetic-data warning and limitations remain.
- **Settings:** existing validated threshold endpoint, session-only reviewer credential and browser API URL preference.
- Secret-like evidence remains passed through the existing `safeJson` redaction helper. No frontend tool execution or security-policy change.

Loading/error/empty/unavailable states use backend outcomes rather than invented values. Pending approval access without credentials correctly remains locked (backend returns 401).

## Accessibility and motion

Semantic main/navigation landmarks, visible focus, `aria-current`, labelled inputs, named icon controls, text-supported statuses, accessible architecture diagram, keyboard drawer focus management and reduced-motion rules. Mobile and table overflow were inspected in the live browser. Screen-reader conformance beyond the accessibility tree and keyboard interactions described in the verification report was not formally audited.

## Verification

Fresh production build on Node 22.14.0; `npm run typecheck`, `npm run lint`, `npm test`, and `npm run build` pass. Frontend test suite: 18 passed across 3 files (including mobile drawer Escape/focus-wrap tests). Relevant backend regression slice: 77 passed (`tests/test_api.py`, `tests/test_approvals.py`, `tests/test_guard.py`, `tests/test_autogen_adapter.py`, `tests/test_phase6.py`). Established Phase 6 full-suite baseline remains 165 passed, 1 skipped, 1 pre-existing failure; no full Python suite was repeated for this frontend-only change.

Live production-mode Next server and FastAPI backend were started locally. The browser verified actual persisted Overview and actions/events/sessions/settings/chat, model/evaluation and Phase 6 API values; `GET /evaluation/phase6` returns 200 and the rendered values match the actual report. `n/a` appears for the sandbox F1 null. Approval queue returns expected 401 without reviewer credentials. Browser console/network has no app exceptions; expected unauthorized approval-queue 401s are present. At 1440×900, 1280×800, 768×1024 and 390×844 there is no document-level horizontal overflow; mobile drawer interactions were exercised. Real screenshots are at [reports/frontend-redesign-overview-desktop.png](../reports/frontend-redesign-overview-desktop.png) and [reports/frontend-redesign-mobile.png](../reports/frontend-redesign-mobile.png), plus [reports/frontend-redesign-desktop.png](../reports/frontend-redesign-desktop.png). Tablet screenshot capture was blocked by the desktop browser host reporting that its window was not currently drawn; tablet DOM/viewport layout was still checked. The project preview servers remain running at `127.0.0.1:3000` and `127.0.0.1:8000`.

The existing Phase 6 report records the live AutoGen smoke and prior dashboard verification; this redesign did not rerun the live model conversation. The known Phase 6 generalization limitation (only three authored test families) and pre-existing test failure remain unchanged and documented in `reports/PHASE6_FINAL_VERIFICATION.md`.

# UX Contract

## Product context

- **Audience:** Operators and developers managing OSA agents and deployments.
- **Primary jobs:** Find an agent, inspect its state, create immutable
  versions, manage lifecycle, inspect resources, and test configured runtime
  access.
- **Target market(s):** General technical/admin use; no Japan-specific market
  contract is defined.
- **Active locales:** English (`en`) is the current supported UI locale.
- **Language/content register:** Plain English, sentence-case actions, with
  future translations required to preserve meaning and accessible names.
- **Timezone/calendar policy:** API timestamps are UTC ISO strings; display
  timestamps use the browser locale/timezone through `Intl`.
- **Accessibility target:** WCAG 2.2 AA.

## Business-context sources

| Domain / scope | Authoritative source | Source type | Reviewed date |
|---|---|---|---|
| Permission model | `docs/guides/security.md`, `generic-agent/src/osa/generic_agent/auth.py` | Security guide / implementation contract | 2026-09-05 |
| Data lifecycle | `docs/API.md`, `docs/ARCHITECTURE.md` | API / architecture contract | 2026-09-05 |
| Deployment and runtime access | `docs/adrs/008-runtime-access.md` | ADR | 2026-09-05 |
| Version snapshots | `docs/API.md`, `control-plane/backend/src/osa/control_plane/backend/agent_catalog.py` | API / domain contract | 2026-09-05 |
| Product scope | `PROJECT_DEFINITION.md`, `TODO.md` | Product definition / backlog | 2026-09-05 |

## Visual contract

- **Project `DESIGN.md`:** `DESIGN.md`
- **Token ownership model:** `DESIGN.md` mirrors the established runtime CSS;
  CSS is canonical until a token export is introduced.
- **Runtime design-system/token source:**
  `control-plane/frontend/src/styles.css`
- **Mapping/export/adapters:** Direct CSS selectors; no generated adapter yet.
- **Token drift gate:** Compare `DESIGN.md` values with the shared stylesheet
  during UI review and run the design audit.
- **Supported themes:** Light and `prefers-color-scheme: dark`.
- **Design-context owner/review policy:** Changes to shared tokens or
  cross-screen behavior update both files in the same changeset.

## Canonical UI Map

| Capability | Canonical owner | Source of truth | Allowed variants | Verification |
|---|---|---|---|---|
| Table Selection | Not applicable; tables are read-only | API and page contracts | none | table semantics tests |
| Select/Listbox | Native `<select>` | browser semantics + page contract | native | keyboard/component tests |
| Date | `<time>` plus `formatTimestamp` | API ISO timestamp contract | typed display | component tests |
| Form | Native form + page validation | API schemas and page flow | create / edit | validation tests |
| Scrollbar | Global application stylesheet | `DESIGN.md` and `styles.css` | geometry exceptions | static audit + browser check |
| Toast | Inline `.state-card` feedback | page flow contract | success / error / status | component tests |
| CRUD | Shared route/service behavior | API contract | return to detail / stay | page and API tests |

## Component behavior

| Component | Default | Hover | Focus | Active | Disabled | Busy | Error |
|---|---|---|---|---|---|---|---|
| Button | Solid navy or bordered secondary | tonal change | visible outline | same geometry | native disabled + not-allowed | label retains width | inline alert when action fails |
| Icon button | Not used; text labels required | n/a | n/a | n/a | n/a | n/a | n/a |
| Input | Labeled native input | border emphasis | visible outline | n/a | native disabled | remains same size | inline field/form message |
| Secret input | Password-masked token field | border emphasis | visible outline | n/a | native disabled | remains same size | inline connection error |
| Search | Submit-based filter with visible label | border emphasis | visible outline | n/a | native disabled | page loading state | retryable inline alert |
| Textarea | Labeled native textarea | border emphasis | visible outline | n/a | native disabled | remains same size | inline validation/error |
| Table/list | Native table or cards | link/action emphasis | native focus | n/a | n/a | stable loading card | retryable inline alert |

## Dataset navigation

- **Admin tables:** Server-backed filtering and pagination; the current UI
  gathers bounded pages for the agent picker and list display.
- **Exploratory lists:** Resource and template cards use explicit search or
  kind selection.
- **URL state:** Agent and selected-deployment filters are shareable query
  state; transient form drafts and tokens are not persisted.
- **Page size:** API default 50; UI requests bounded 100-record pages with a
  maximum of 10 pages.
- **Empty/no-results/error/loading treatment:** Stable `.state-card` blocks
  with direct next actions and retry where recovery is possible.
- **Back/scroll restoration:** Route changes scroll to the top and focus the
  main landmark; native browser back remains available.
- **Selection scope:** No bulk selection is currently exposed.

## Flow ledger

| Operation | Trigger | Pending | Success destination | Success feedback | Failure recovery | Focus outcome | Source ref |
|---|---|---|---|---|---|---|---|
| Create | Create-agent form | Button label + disabled fields | Agent detail | inline success/detail | inline validation or retry | route focus on detail | `docs/API.md` |
| Edit | API-backed immutable update/version flow | page action state | owning detail page | inline status | preserve state + retry | action remains visible | `docs/API.md` |
| Delete | API only; no UI bulk delete | API request | caller-controlled | API response | typed API error | caller-controlled | `docs/API.md` |
| Search | Submit filter form | stable loading card | same list | updated count | retry | main landmark | `styles.css` |
| Bulk action | Not applicable | n/a | n/a | n/a | n/a | n/a | product scope |
| Cancel/back | Native link/button | none | prior/list route | none | browser navigation | route focus | `App.tsx` |
| Soft-delete | Archive confirmation | confirmation group | detail page | inline success | cancel/retry | confirmation remains nearby | `docs/API.md` |
| Hard-delete (irreversible) | Not exposed in UI | n/a | n/a | n/a | n/a | n/a | product scope |

## Navigation and responsive behavior

- **Route document title policy:** The shell title is stable today; route
  titles are a future localized enhancement.
- **Route error / 403 page behavior:** API error cards preserve context;
  server authorization remains authoritative and rejected tokens clear from
  the session.
- **Breadcrumb/tab/route-state policy:** Back links and query parameters are
  used where the owning list/detail relationship is meaningful.
- **Sidebar/drawer/bottom-sheet transformation:** The 220px sidebar becomes a
  horizontal scrollable navigation strip below 760px.
- **Responsive table strategy:** Tables scroll horizontally inside
  `.table-wrap`; cards collapse through auto-fit grids.
- **Truncation/full-value access:** Technical values wrap or use a scrollable
  `<pre>`; safe definitions can be opened explicitly.
- **Focus restoration and sticky-obstruction policy:** Skip link and route
  focus target the main content; sticky top navigation remains above content.

## Overlays and feedback

- **Dialog primitive:** Inline confirmation group is the current canonical
  variant; browser dialogs are forbidden.
- **Destructive confirmation levels:** Archive and rollback require explicit
  confirmation and name the consequence.
- **Toast placement/duration/deduplication:** No toast provider; inline status
  cards are persistent until the next action.
- **Alert/banner scope:** Errors are local to the failing page/section and use
  `role="alert"`; loading/status use `role="status"`.
- **Unsaved-changes behavior:** Current forms are explicit submit flows; no
  navigation guard is needed until durable drafts are introduced.
- **Layer/z-index contract:** No floating product overlays today; sticky
  navigation and skip link use the documented shell order.

## Async and resilience

- **Mutation default:** Pessimistic; update local state after the API returns.
- **Idempotency and duplicate-submit policy:** Disable the active submit/action
  while a request is pending.
- **Auto-save/draft recovery:** Not used; agent drafts are explicit API state.
- **Offline/read-stale/write behavior:** Show an inline error and retain the
  current form/list context; no optimistic writes.
- **Retry/backoff/timeout behavior:** API requests have abort deadlines;
  retry is explicit and page-owned.
- **Version conflict and multi-tab behavior:** Surface typed 409 responses;
  reload the owning record when the user retries.
- **Session expiry/re-authentication:** A 401/403 clears the rejected token
  and returns the shell to anonymous mode.
- **Stale-request cancellation/invalidation:** Effects guard unmounted pages;
  API requests use abort deadlines.

## Validation

- **Schema/validation layer:** TypeScript page checks plus server Pydantic
  schemas; server is authoritative.
- **Trigger timing:** Submit-time for forms; immediate selection changes for
  catalog tabs.
- **Error summary/inline policy:** Inline alert with a clear corrective action.
- **Server error mapping:** `ApiError` presents stable OSA error code/message.
- **Sensitive-value handling:** Bearer tokens stay in session storage only;
  safe snapshot/definition views redact sensitive keys.
- **Form safeguards:** Native labels, explicit submit handling, disabled
  pending controls, and no native browser validation bubbles.

## Permission and clipboard

- **Permission UI strategy:** Server controls access; visible API failures are
  presented inline rather than inferred from client-only role checks.
- **Clipboard copy policy:** No secret-copy control is currently exposed.
- **Disabled-state explanation:** Labels and nearby status text explain busy or
  unavailable controls where the reason is not obvious.

## Verification

- **Required static commands:** `npm run typecheck`, `npm run test`,
  `npm run build`, plus the repository Python gates.
- **Browser/device/theme matrix:** Desktop and 320px-wide viewport, keyboard
  route navigation, light/dark theme, long technical values, and reduced
  motion.
- **Accessibility checks:** Native semantics, visible focus, skip link, live
  status/error regions, and no horizontal page overflow at 320px.
- **Component-state coverage:** Testing Library page tests plus the frontend
  production build and container deep-link smoke test.
- **Canonical sibling flow:** Agent detail lifecycle/version history compared
  with resource/deployment cards and their shared state-card language.

# Browser regression follow-up, 16–18 October 2026

Scope: the supplied repeat QA report. All mutations and automated checks use a
fresh local PostgreSQL/Redis stack with synthetic fixtures. The listed staging
orders, staff invitations and QA catalog records are not modified. Changes are
for PR review; no merge or production/staging deployment is requested here.

## Causes and changes

| Finding | Cause | Change |
|---|---|---|
| Contact rejected for allowed roles | Contact BFF UUID regex had four groups instead of UUID's five; valid requests never reached the API | Correct validation; route unit regression plus allowed-role and denied-role server checks; audited API remains authoritative |
| Homepage calculator dead end | Shared planner rendered without organization/model/branch | Homepage offers explicit organization links; actual calculator lives on the selected storefront |
| Overlapping management forms | Independent details with absolute/fixed forms | Native modal dialogs with inert background, scroll, Escape, close/cancel and restored trigger focus; input stays mounted when closed |
| Old success beside phone conflict | Parent success and child error had independent lifetimes | Clear success on a new attempt, guard duplicate submit, localize conflict |
| Generic date error | UI collapsed 422s into one sentence | Stable domain error codes, branch-timezone/inclusive-day client validation, linked field errors and safe Russian fallback; server recalculates |
| FAQ contradiction | Homepage retained pre-tariff copy | Main notice/FAQ and tenant storefront explain synthetic server pricing, no reservation |
| Lost tenant | Membership bootstrap always chose first allowed item | Persist selected slug and link it from storefront; resolve only against current server memberships; clear tenant-specific components and stale requests |
| Dashboard/list mismatch | Date-based count opened a status-only filter | Handover-due and returns-today queries repeat aggregate date predicates in each branch timezone, including delayed handovers |
| Stale inventory | Order commands refreshed queue/dashboard only | Refresh authorized catalog after successful command; active-order asset edits still refused under asset row lock |
| Stale cross-tab session DOM | HttpOnly cookie changes were not observable by loaded React state | Login/logout/activation broadcasts; clear protected state before server refresh; focus/visibility revalidation and bounded login/session requests |
| Invitation lacks context/used form visible | Page checked token syntax only | POST preview with minimal DB-verified organization/name/role/expiry; unavailable links show no credential form; atomic redeem retained |
| Existing records cannot be edited in UI | PATCH routes existed without edit forms | Owner/admin dialogs for model name/description/kit, tariff/day/deposit/limits, asset code/model/branch, branch name/city/timezone/fulfillment; snapshot remains immutable |
| Missing mobile landlord route/technical copy | Header navigation hidden on small screens; infrastructure wording in copy | Footer landlord link retains organization; remove PostgreSQL/tenant-scoped customer text |

## Privacy and role audit

See [access matrix and recovery procedure](../security/access-control-matrix.md).
No permissions were broadened. Public catalog/SSR use only the public read model;
contacts require explicit audited commands and private responses remain no-store.
Invitation tokens remain in fragments until removed, then travel only in POST
bodies. Invitation page uses no-referrer and no third-party analytics. HTTP logs
record route templates, method, status and bounded correlation ID, not bodies or
contacts. Validation errors omit submitted values.

## Deliberate limits and remaining work

- No real SMS, payments, invitation delivery, public customer booking or account
  recovery integration is enabled. Local memory-provider tests are not live SMS
  acceptance tests.
- Staff UI supports deciding **customer-created** extension requests. There is no
  arbitrary staff extension or reverse-pickup routing flow. A separate product
  decision should define authority, customer consent, revised price and audit
  before exposing a manual extension command.
- Tenant phone/hours/pickup address/delivery zones and fees/legal documents/model
  manuals still require real owner-approved content and a publication-readiness
  gate. Missing information is a demo limitation; none is invented here.
- Account recovery currently uses replacement invited access, documented above;
  existing credential recovery remains disabled.
- Native confirmation is retained for lifecycle mutations. Browser automation
  explicitly accepts these confirmations; a tool waiting for manual confirmation
  is not counted as an application defect.
- Cross-tab notification applies to this browser/origin; external cookie changes
  are detected on focus/resume or the next authorized request. Full server-push
  session invalidation is not implemented.
- Automated screenshots and tests validate the local build. A subsequent
  authorized staging review must verify Traefik/Basic Auth, the QA courier's
  actual credentials, and their assigned delivery. No live staging cycle is
  claimed.

## Completed validation

- Backend: **70 passed**, coverage **89.77%** (required ≥85%), Python 3.12,
  PostgreSQL 16 under the runtime role and Redis 7. Dedicated disposable database
  `photolamp_backend_tests`, separate from browser data. One existing dependency
  deprecation warning; no failing tests.
- After the migration round-trip, the focused access/auth/order suite passed
  again: **5 passed**. Alembic `downgrade base` → `upgrade head` succeeded.
- Frontend: **49 unit tests passed**, ESLint, Prettier, strict TypeScript and
  Next.js production build passed.
- Browser: **105 passed** in one final full run across 320/430/1440 px, including
  explicit dialog checks at **390×844, 768×1024 and 1440×1024** and axe checks.
- API/web readiness returned 200 with healthy PostgreSQL/Redis. `git diff --check`
  passed. Screenshots were visually inspected.

Browser API mocks test UI branches; the separate contact browser regression and
backend suite exercise real local handlers and database guards. A previous local
run exposed fixture cleanup assumptions when browser records and backend tests
shared a database; the final runs use separate databases, as documented in local
development instructions. The final browser run is also separate from other
Playwright invocations so trace/output directories cannot race.

These are local results; GitHub CI is reported separately on the PR. No remote
staging acceptance or production deployment is implied.

## Local screenshots

Only synthetic data is shown. Contact screenshot comes from the real local BFF,
API and encrypted order; the test cancels its reservation afterwards.

[Organization choice](screenshots/organization-choice.png) ·
[Minimum period validation](screenshots/minimum-days.png) ·
[Single dialog at 390×844](screenshots/dialog-390.png) ·
[Authorized synthetic contact](screenshots/contact-synthetic.png).

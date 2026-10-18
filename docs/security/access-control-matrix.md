# Current access contract

Reviewed against the HTTP handlers, ADR 0002/0004/0009–0017/0024 and isolated
integration tests. This describes the current demo, not an authorization to add
roles or enable customer checkout in production.

Every private request validates an unexpired, unrevoked session, active user and
membership. URL UUIDs are selectors, never credentials. A foreign tenant is 404;
a forbidden staff role is 403; missing/expired/revoked sessions are 401. Runtime
PostgreSQL RLS and composite tenant foreign keys remain mandatory.

| Resource/action | Guest | Customer | Owner | Admin | Operator | Courier |
|---|---|---|---|---|---|---|
| Public published catalog/quote | Read | Read | Read | Read | Read | Read |
| Staff order queue/detail/dashboard | Deny | Deny | Read | Read | Read | Deny |
| Phone order/confirm/cancel/handover/return/inspection | Deny | Deny | Allowed state transitions | Same | Same | Deny |
| Customer order/detail/extension/return request | Deny | Own orders in current tenant | No impersonation | No impersonation | No impersonation | Deny |
| Staff contact reveal | Deny | Deny | Audited, eligible order | Same | Same | Deny |
| Courier task/contact | Deny | Deny | Dispatch view | Dispatch view | Dispatch view | Only current assigned task and allowed stage; audited contact |
| Assign/unassign courier | Deny | Deny | Allowed | Allowed | Allowed | Deny |
| Internal catalog read | Public projection only | Public projection only | Full | Full | Minimal order-entry projection | Deny |
| Models/tariffs/assets/branches create/edit/status | Deny | Deny | Allowed | Allowed | Deny | Deny |
| Team read | Deny | Deny | Allowed | Read only | Deny | Deny |
| Team edits/invitations/revocation | Deny | Deny | Allowed with last-owner/self guards | Deny | Deny | Deny |
| Staff session inventory/revocation | Deny | Deny | Own | Own | Own | Own |
| Invitation preview/redemption | Token capability only | Same | Same | Same | Same | Same |

Customer sessions are not staff sessions, even when a phone happens to match.
The common demo identity can manage only its current demo session. A courier
cannot get an arbitrary order by UUID, use staff contact-access, or take another
courier's task. Owner/admin/operator cannot invent a courier membership.
Invitation role/name/organization come from the database, not URL parameters.
The token is a one-time capability: preview confers no staff session; redemption
still locks and atomically consumes the invitation.

## Evidence

- `test_tenancy.py`: foreign tenant URL/header, absent scope, RLS CRUD, composite
  boundaries, pool scope reset, invalid/expired/revoked session.
- `test_admin_catalog.py`: full/minimal/denied read projections, owner mutations,
  operator/courier denial, publication prerequisites, production demo gating.
- `test_phone_orders.py`: owner/admin/operator order reads and contact access,
  operator direct-UUID mutation denial (including publication/archive prechecks), courier and disabled member denial,
  foreign order, encrypted contacts, replay/audit, stale asset mutation refusal,
  handover → return → inspection and inventory release.
- `test_admin_orders.py`, `test_order_requests.py`: lifecycle/idempotency, immutable
  snapshots, rejected transitions and extensions, availability conflict,
  dashboard privacy/role guards and date-aware drill-down.
- `test_courier.py`: unrelated assignee/foreign tenant/task UUID denial, courier
  lifecycle and audited assigned-contact access.
- `test_staff_auth.py`: invitation preview, simultaneous redemption (one 200,
  one 409), used/revoked/expired links, disabled membership, login lockout.
- `test_admin_team.py`, `test_staff_sessions.py`: current permissions checked
  after membership changes, last owner, foreign session UUID and revocation.
- `test_customer_auth.py`, `test_checkout_orders.py`: own-order and capability
  isolation in local fixtures; these do not enable public booking or real SMS.
- `test_catalog.py`, `test_observability.py`: public DTO and structured route logs
  omit contacts, asset secrets and capabilities. Validation responses now omit
  submitted input entirely, including password/token values.

Browser coverage complements these server checks. Hiding a section is not an
access control. BroadcastChannel clears stale UI after app login/logout in another
tab; focus/visibility recovery rereads the server session. In-flight catalog
responses from an invalidated context cannot restore old data. Offline or blocked
background tabs recover when resumed; no instantaneous server push is claimed.

## Recovery using existing contracts

There is no safe password-reset or login-reminder endpoint in this release. Do
not query credential tables for an end-user report, reveal hashes/passwords, add a
shared password, or impersonate the account.

For a lost synthetic demo staff account, an active owner verifies the intended
person and membership out of band, disables that old membership through Team,
and creates a replacement invitation with the minimum necessary role. The user
opens the one-time link and chooses a **new unique login and their own password**.
Historical orders/audit retain the original user ID; assignments must be
explicitly reassigned to the replacement courier through Dispatch. Do not delete
the old user or rewrite order history. This is replacement access, not recovery
of the old credential. An administrator must ask an owner to perform it.

Before real accounts: design a dedicated owner-authorized recovery flow with
verified recipient identity, generic responses, rate limits, short-lived
single-use reset capability, session revocation and audit of request/approval/
completion. Reminding a login also requires identity verification. No provider
messages, new reset links or credentials are sent by this change.

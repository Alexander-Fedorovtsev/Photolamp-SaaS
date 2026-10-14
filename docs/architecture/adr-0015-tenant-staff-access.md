# ADR 0015: Tenant staff directory and role controls

## Status

Accepted, 2026-10-14.

## Context

An owner needs to understand who can access a tenant and change day-to-day roles
without editing PostgreSQL. Production staff authentication and invitation delivery
have not been selected yet, so this stage must not pretend that SMS invitations or
new staff login are available.

## Decision

- `tenant_memberships` remains the authorization source and gains a tenant-specific
  display label plus an update timestamp. The directory response contains no phone,
  email, authentication token or session data.
- Owners and admins can read the directory. Only an active owner can change a
  membership label, role or active/disabled state. Operators and couriers cannot
  read the directory.
- A user cannot change their own role or access state from the current session. A
  tenant must retain at least one active owner. Team mutations take a tenant-scoped
  PostgreSQL advisory lock and re-check the actor after acquiring it, preventing two
  concurrent owner changes from bypassing those rules.
- Memberships are disabled rather than deleted. Existing order and audit foreign
  keys therefore retain their historical staff actor. Authorization functions
  already reject disabled memberships on the next request.
- Every effective change writes `staff.updated` to the tenant RLS audit stream with
  privacy-safe before/after membership fields.
- Demo tenants receive named synthetic owner/operator/courier/admin memberships.
  Only the existing synthetic owner has a demo session. No invitation or login is
  created for the additional records.

## Consequences

The owner can now administer current tenant access safely and review meaningful
demo roles without exposing global user identity data. Role changes take effect on
the next authorized request and retain an audit trail.

Adding a new person remains blocked until production authentication is chosen. A
later stage must define identity proofing, invitation expiry and redemption,
recovery, MFA or passkeys, session management and delivery channels before exposing
an invite action.

# ADR 0017: Staff session inventory and revocation

## Status

Accepted, 2026-10-14.

## Context

ADR 0016 introduced durable staff credentials and opaque sessions, but a staff
member could end only the session represented by the current browser cookie. A
lost or shared device therefore required database intervention. Session controls
must remain global to the authenticated identity while tenant authorization stays
scoped to active memberships.

## Decision

- An authenticated staff identity can list its own active, unexpired sessions,
  revoke one session by opaque UUID, or revoke every password session including
  the current one. A revoked token fails on the next API request.
- The database resolves ownership from the current token hash inside narrow
  `SECURITY DEFINER` functions. The caller cannot supply a user ID, query another
  identity, read `auth_sessions`, or receive a token hash.
- Session metadata contains only a coarse browser/OS label, creation time,
  last-seen time and expiry. The API reduces User-Agent to labels such as
  `Safari · macOS`; it does not persist IP address, full User-Agent, versions,
  geolocation or a device fingerprint. Last-seen writes are coalesced to at most
  once every five minutes during tenant-authorized activity.
- Password sessions and environment-gated demo sessions have separate kinds.
  Password sessions for one identity are manageable together. A shared demo
  identity sees and can revoke only its current demo session, preventing one demo
  visitor from controlling another visitor's browser.
- Individual and global revocations create privacy-safe audit events for every
  active tenant membership. Audit changes contain only revocation outcome or
  count, never token material or client labels.
- The Next.js BFF deletes the HttpOnly cookie when the current session is revoked.
  Session responses and proxy routes use `Cache-Control: no-store`.

## Consequences and limits

Staff can now respond to a lost device without operator access to the database.
Disabling a membership still removes tenant authorization independently of the
global session inventory.

Revoked and expired rows remain server-side until a future retention job is
defined; they are never returned by the inventory. Password recovery,
MFA/passkeys, login security notifications and automated SMS delivery remain
separate production stages.

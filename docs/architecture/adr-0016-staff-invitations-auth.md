# ADR 0016: One-time staff invitations and password authentication

## Status

Accepted, 2026-10-14.

## Context

Tenant owners can manage existing memberships, but adding a real employee was
blocked because demo authentication cannot establish a durable staff identity.
An SMS provider is intentionally unavailable. The next increment needs a working
authentication path without pretending that an external delivery integration is
configured or collecting a phone number merely to satisfy the original user schema.

## Decision

- An active tenant owner can create an invited membership with a display label and
  role. The API returns a cryptographically random invitation token exactly once;
  PostgreSQL stores only its SHA-256 digest. The default expiry is 48 hours and the
  hard database maximum is seven days.
- The web UI puts the token in the URL fragment, removes the fragment after mount,
  and asks the owner to transfer the link through a channel they already trust. No
  SMS or email is claimed or queued.
- Redemption is atomic: it locks a pending invitation, verifies expiry and the
  invited membership, reserves a normalized non-personal login, stores a salted
  scrypt password hash, activates the membership, consumes the invitation and
  issues an opaque session. A replay, revoked token, expired token or duplicate
  login fails without partial state.
- Staff login uses a generic failure response, Redis rate limiting and a database
  lock after five failed password attempts for 15 minutes. Unknown logins still run
  a dummy scrypt verification to reduce timing-based enumeration. Session tokens
  are stored only as SHA-256 digests and reach the browser only through an
  `HttpOnly`, `SameSite=Strict` cookie.
- `staff_credentials` is a global authentication table with no runtime table grant.
  Narrow `SECURITY DEFINER` functions expose only lookup, failure accounting and
  session issuance. `staff_invitations` is tenant-scoped with FORCE RLS. Creation,
  acceptance and revocation write privacy-safe audit events without login, password
  hash or plaintext token.
- Staff users no longer require a phone number. Existing customer and phone-order
  identities keep their current phone handling; this change avoids inventing staff
  contact data.

## Consequences and limits

The platform now has a working staff onboarding and repeat-login path independent
of an external provider. Owners can revoke an unused link, and disabling a
membership invalidates authorization on the next request while preserving history.

Automated delivery, password recovery, MFA/passkeys, session inventory, forced
logout of all sessions and security notifications remain required before a broad
production launch. The current UI states these limits. A later SMS adapter may
deliver the same invitation capability but must never persist or log its plaintext
token.

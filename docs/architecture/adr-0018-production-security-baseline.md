# ADR 0018: Production web and dependency security baseline

## Status

Accepted, 2026-10-14.

## Context

The staging deployment already isolates application services, verifies an exact
CI-approved revision and keeps staff tokens in an HttpOnly cookie. Production
readiness also requires automated runtime dependency review, explicit browser
boundaries and a cookie policy that cannot silently become insecure because a
proxy header is absent.

## Decision

- Python runtime dependencies are exported from the committed `uv.lock` and
  checked by pinned `pip-audit 2.10.1` in the backend CI job before code checks.
  The directly used `cryptography` package is constrained to the audited 50.x
  line; dependency findings fail the quality gate.
- Next.js emits a restrictive baseline CSP plus HSTS, COOP, CORP, nosniff,
  frame, referrer and permissions headers on every route. The production policy
  allows only same-origin resources, local data/blob images and the inline
  scripts/styles currently required by Next.js. Development alone permits the
  eval and WebSocket sources required by hot reload.
- A single Next.js proxy guards every state-changing `/api/admin/*` request.
  `POST`, `PUT`, `PATCH` and `DELETE` require an `Origin` matching the effective
  protocol and host. The public API remains unreachable from the edge; the BFF
  continues to attach the opaque staff token only on the private Docker network.
- Staff session cookies are always `Secure` in explicit staging and production
  environments. Development may use HTTP; HTTPS development still receives a
  Secure cookie. The staging topology validator rejects a web container that is
  not explicitly configured with `APP_ENV=staging`.
- Security controls have unit, browser and resolved-Compose tests. Responses
  rejecting an origin do not include session state and are not cacheable.

## Consequences and limits

The CI gate now detects known vulnerabilities in Python runtime packages and
the browser boundary no longer relies only on SameSite cookie behavior. Existing
same-origin admin UI behavior is unchanged.

The CSP still permits inline scripts and styles for the current Next.js runtime;
a nonce-based policy can replace that allowance when the rendering boundary is
prepared for per-request nonces. Container/base-image scanning, owner MFA,
account recovery, backups, contact-key rotation, retention and production
observability remain separate production stages. SMS stays disabled.

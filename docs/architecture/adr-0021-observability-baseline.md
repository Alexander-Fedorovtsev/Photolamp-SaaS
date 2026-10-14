# ADR 0021: observability baseline and staging monitor

## Status

Accepted and implemented for the single-host staging environment.

## Context

Readiness and CI/CD already stopped unhealthy releases, but an operator could not
correlate a failed browser request with API logs, inspect bounded service metrics,
or detect a later outage and stale backup without logging into the host. Adding a
hosted APM, log store or paging provider before one is selected would create a
misleading integration and additional access to potentially sensitive data.

Multi-tenant telemetry must not turn tenant identifiers, storefront slugs,
customer contacts, request bodies, query strings, authorization data or database
parameters into labels or logs.

## Decision

- Every API response has `X-Request-ID`. A caller-supplied ID is retained only
  when it matches a bounded ASCII format; otherwise the API generates a random ID.
  The Next.js health/version routes and staff BFF pass the ID to FastAPI and return
  it to the caller.
- FastAPI writes one JSON completion event with the request ID, method, route
  template, status and duration. Unhandled exceptions are logged only by class and
  become a generic response. SQLAlchemy hides bound parameter values.
- A process-local Prometheus registry counts requests, duration, in-flight work
  and unhandled exception classes. Labels are limited to method, route template,
  status and exception class. Build metadata contains only environment and Git SHA.
- `/internal/metrics` is absent from OpenAPI and is reachable only inside the API
  container/network. The staging API publishes no host port, Next.js does not
  proxy this path, and the monitor verifies that the public edge returns `404`.
- `Monitor staging` runs every 15 minutes and on demand. It reuses the verified
  SSH credentials and the `photolamp-staging` concurrency lock, requires the host
  checkout to match the exact `main` SHA, and checks container health, internal
  metrics, HTTPS readiness, request-ID propagation, web/API SHA agreement, latest
  encrypted-backup checksum and age, and backup disk utilization.
- A deploy runs the same monitor after creating and restoring its encrypted
  backup. A failed check therefore blocks a healthy CD result. Later scheduled
  failures remain visible as failed GitHub Actions runs.

## Privacy and cardinality

Raw URL paths and query strings are excluded because both can carry tenant or
record identifiers. The route label is the framework template such as
`/api/v1/r/{tenant_slug}/catalog`. Headers, bodies, cookies, tokens, IP addresses,
user agents, exception messages and database values are not part of HTTP telemetry.
Request IDs are correlation capabilities, not customer or tenant identities.

## Consequences and limitations

This baseline gives deploy-time and periodic detection without new secrets or a
claim that an external monitoring provider is active. Prometheus values and JSON
logs are currently process-local/container stdout: they have no durable time-series
or centralized search after container replacement. GitHub Actions is the current
failure signal; delivery depends on repository notification settings and there is
no dedicated on-call channel, escalation policy or SLO burn-rate alert.

The scheduled probe uses GitHub and the same server/SSH path as deployment. It is
useful for host, TLS, application, revision and backup failures, but it is not an
independent multi-region availability probe. Before real production traffic,
select a log/metrics platform with retention and access controls, add an external
uptime location and a tested on-call destination, then define service-level
objectives and alerts from measured traffic.

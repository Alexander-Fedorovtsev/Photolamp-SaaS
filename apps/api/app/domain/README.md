# Domain boundary

Tenant identity and RLS infrastructure are implemented in `app.tenancy`,
`app.database` and migration `0002`. Catalog/inventory tables and the privacy-safe
public read model are implemented by migration `0003` and `app.catalog`. Migration
`0004`, `app.auth` and `app.admin_catalog` add environment-gated synthetic staff
sessions, auditable tenant catalog CRUD and the protected admin read-model. Availability,
server quotes, atomic inventory holds, anonymous expiring draft orders and a local/test
OTP identity boundary are implemented without plaintext customer phone storage. A customer
session can read only its own privacy-minimized order history. Staff order operations use
immutable snapshots, idempotent events and atomic `held` to `confirmed` or `cancelled`
transitions. Fulfillment continues through handover, return and inspection while the asset
moves through `rented` and `inspection`; contact decryption is a separate audited command.
Verified customers can create extension and return requests for their own active order.
Staff decisions preserve the immutable snapshot, recheck allocation conflicts and expose
overdue as a derived branch-local fact. Notification jobs are durable and deduplicated but
remain explicitly blocked while the provider is disabled.
Staff invitation links and repeat password login are implemented without an external
delivery service. No real SMS provider, password recovery, MFA, public confirmation or
payment exists; the disabled and local/test memory SMS adapters preserve the future provider port.
Add domain modules here only with their migrations, authorization and critical isolation
tests.

Future services receive an immutable authorized tenant scope, established from a
verified session and active membership. Public slug resolution grants access only
to explicitly public DTOs. URL parameters and headers never establish permission.
See ADR 0002 for identity/RLS, ADR 0003 for catalog boundaries, ADR 0004 for the
explicit demo staff boundary and ADR 0007 for the anonymous checkout boundary.
ADR 0008 defines the customer OTP and provider boundary. ADR 0009 defines the customer
order read model. ADR 0010 defines staff order operations and immutable snapshots.
ADR 0011 defines encrypted phone-order contacts. ADR 0012 defines fulfillment and
controlled contact access. ADR 0013 defines post-order requests, overdue calculation
and the fail-closed notification outbox.

# Domain boundary

Tenant identity and RLS infrastructure are implemented in `app.tenancy`,
`app.database` and migration `0002`. Catalog/inventory tables and the privacy-safe
public read model are implemented by migration `0003` and `app.catalog`. Migration
`0004`, `app.auth` and `app.admin_catalog` add environment-gated synthetic staff
sessions, auditable tenant catalog CRUD and the protected admin read-model. Availability,
server quotes, atomic inventory holds and anonymous expiring draft orders are implemented
without customer PII. No real customer login/OTP, confirmed booking or payment exists.
Add domain modules here only with their migrations, authorization and critical isolation
tests.

Future services receive an immutable authorized tenant scope, established from a
verified session and active membership. Public slug resolution grants access only
to explicitly public DTOs. URL parameters and headers never establish permission.
See ADR 0002 for identity/RLS, ADR 0003 for catalog boundaries, ADR 0004 for the
explicit demo staff boundary and ADR 0007 for the anonymous checkout boundary.

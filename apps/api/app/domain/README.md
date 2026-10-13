# Domain boundary

Tenant identity and RLS infrastructure are implemented in `app.tenancy`,
`app.database` and migration `0002`. Catalog/inventory tables and the privacy-safe
public read model are implemented by migration `0003` and `app.catalog`. No public
login/OTP issuance, date availability or booking domain exists yet. Add domain
modules here only with their migrations, authorization and critical isolation tests.

Future services receive an immutable authorized tenant scope, established from a
verified session and active membership. Public slug resolution grants access only
to explicitly public DTOs. URL parameters and headers never establish permission.
See ADR 0002 for identity/RLS and ADR 0003 for catalog boundaries.

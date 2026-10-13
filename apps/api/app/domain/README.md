# Domain boundary

Tenant identity and RLS infrastructure are implemented in `app.tenancy`,
`app.database` and migration `0002`. No public login/OTP issuance or booking
domain exists yet. Add domain modules here only with their migrations,
authorization and critical isolation tests.

Future services receive an immutable authorized tenant scope, established from a
verified session and active membership. Public slug resolution grants access only
to explicitly public DTOs. URL parameters and headers never establish permission.
See `docs/architecture/adr-0002-tenant-identity-rls.md` for the enforced contract.

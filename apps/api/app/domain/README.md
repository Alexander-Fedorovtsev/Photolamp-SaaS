# Domain boundary

No booking or identity domain is implemented in foundation. Add modules here only
with their migrations, authorization and critical tests.

Future services receive an immutable authorized tenant scope, established from a
verified session and active membership. Public slug resolution grants access only
to explicitly public DTOs. URL parameters and headers never establish permission.
See `docs/architecture/adr-0001-foundation.md` for RLS and repository contracts.

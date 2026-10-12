# AGENTS.md — Photolamp-SaaS

## Mission
Build a polished, mobile-first, multi-tenant SaaS for renting phototherapy lamps and, later, other rental equipment. Initial pilot: Ufa, Russia, fleet of fewer than 10 lamps. Primary customer flow: online booking. Equally necessary: orders entered by staff after a phone call. Future commercial model: independently managed rental businesses subscribed to the platform.

## Read before making changes
1. `docs/architecture/saas-tz.md` — domain, security, architecture, database and booking rules.
2. `docs/product/landing-page-spec.md` — conversion-focused landing page and animations.
3. `docs/product/brand-messaging.md` — approved copy direction, voice and medical-claim restrictions.
4. `docs/codex/CODEX_BRIEF.md` — product goals.
5. `docs/codex/IMPLEMENTATION_PLAN.md` — incremental milestone plan.
6. `docs/codex/PROMPTS.md` — reusable task prompts.

These documents are project requirements. Check the existing repository before changing its structure and preserve any working implementation.

## Current priority: Foundation + landing page
Build incrementally:
- PR 1: minimal reproducible foundation (Next.js + TypeScript front end, FastAPI back end scaffold, lint/tests/build CI, local development instructions).
- PR 2: mobile-first public landing page with warm, original cartoon-like illustration or lightweight animated SVG of a family renting a lamp, a courier delivering it, and parents feeling supported at home.
- Calls to action for both online booking and phone orders. Do not invent a business phone number, address, reviews, certifications, medical outcomes or real prices. Store unknown content in typed configuration or explicitly labelled demo data.
- Include a price estimator only if supplied with configured tariffs; otherwise clearly mark demo estimates and do not accept fake checkout.
- Implement accessible UI with keyboard focus, semantic HTML, strong contrast, and `prefers-reduced-motion`; do not sacrifice performance for decorative animation.
- Make reusable, tenant-aware components; no premature marketplace or cross-tenant customer aggregation.

## Medical integrity and privacy
- This is rental software, not a medical diagnosis or treatment service.
- No promises of guaranteed recovery, no fake before/after medical claims, no implied clinical advice. Depict safe, non-instructional scenes; real device use requires manufacturer instructions and clinician guidance.
- Do not collect diagnosis, newborn medical measurements, or other special-category health data in the MVP.
- Later booking and customer data must be protected with tenant-scope enforcement, PostgreSQL RLS, access tests, audit logs and least privilege.
- Never commit secrets, API tokens, SMS provider keys or real customer data.

## Engineering practices
- Work on a feature branch; open a PR rather than pushing development changes straight to `main`.
- Small reviewable commits; update docs as behavior evolves.
- Add automated checks and a useful README with local run/test/build commands.
- Run relevant tests and report exact results in PR description.
- Do not claim integrations are live when they are mocked or merely scaffolded.
- Block booking/charge flows until real server-side availability, pricing and payment state exist.
- Ask for decisions only when required to unblock work; use documented, reversible defaults otherwise.

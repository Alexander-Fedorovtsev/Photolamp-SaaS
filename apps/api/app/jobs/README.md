# Worker boundary (not running)

Redis is provisioned and checked; no job processor, SMS delivery or TTL worker is
active. API transactions create tenant-scoped, deduplicated `notification_jobs`, but
every job remains `blocked/provider_disabled` and no delivery attempt is claimed.
Holds and draft orders are reclaimed lazily by API transactions. Introduce one worker
implementation (for example arq) only with a real notification provider and attempt log.
Every job must carry a tenant ID, establish transaction-local
RLS context, use an idempotency/deduplication key and avoid customer data in logs.
Do not treat this directory as a working integration.

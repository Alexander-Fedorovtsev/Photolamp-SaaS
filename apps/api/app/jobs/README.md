# Worker boundary (not running)

Redis is provisioned and checked; no job processor, notifications or TTL worker
is active. Holds and draft orders are reclaimed lazily by API transactions; introduce
one worker implementation (arq) when proactive expiration or notifications are needed.
Every job must carry a tenant ID, establish transaction-local
RLS context, use an idempotency/deduplication key and avoid customer data in logs.
Do not treat this directory as a working integration.

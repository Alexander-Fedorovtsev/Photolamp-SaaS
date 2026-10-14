"""Order extension/return requests and fail-closed notification jobs.

Revision ID: 0012_order_requests
Revises: 0011_order_fulfillment
"""

from alembic import op

revision = "0012_order_requests"
down_revision = "0011_order_fulfillment"
branch_labels = None
depends_on = None


def execute_script(script: str) -> None:
    for statement in script.split("\n        -- statement\n"):
        op.execute(statement)


def upgrade() -> None:
    execute_script(
        """
        ALTER TABLE order_events ADD COLUMN actor_customer_id uuid;
        -- statement
        ALTER TABLE order_events ADD CONSTRAINT order_events_actor_customer_fk
          FOREIGN KEY (tenant_id, actor_customer_id)
          REFERENCES customers(tenant_id, id);
        -- statement
        ALTER TABLE order_events ADD CONSTRAINT order_events_single_actor_check
          CHECK (num_nonnulls(actor_staff_user_id, actor_customer_id) <= 1);
        -- statement
        ALTER TABLE order_events DROP CONSTRAINT order_events_event_type_check;
        -- statement
        ALTER TABLE order_events ADD CONSTRAINT order_events_event_type_check CHECK (
          event_type IN (
            'draft_created', 'phone_order_created', 'order_confirmed', 'order_cancelled',
            'contact_accessed', 'order_handed_over', 'order_returned',
            'order_inspection_completed', 'order_extension_requested',
            'order_extension_approved', 'order_extension_rejected',
            'order_return_requested', 'order_return_acknowledged', 'order_return_rejected'
          )
        );
        -- statement
        CREATE TABLE order_extension_requests (
          id uuid PRIMARY KEY,
          tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
          order_id uuid NOT NULL,
          customer_id uuid NOT NULL,
          original_end_date date NOT NULL,
          requested_end_date date NOT NULL,
          additional_days integer NOT NULL CHECK (additional_days BETWEEN 1 AND 365),
          amount_minor integer NOT NULL CHECK (amount_minor > 0),
          currency text NOT NULL CHECK (currency = 'RUB'),
          state text NOT NULL DEFAULT 'pending'
            CHECK (state IN ('pending', 'approved', 'rejected')),
          idempotency_key_hash bytea NOT NULL CHECK (octet_length(idempotency_key_hash) = 32),
          command_fingerprint bytea NOT NULL CHECK (octet_length(command_fingerprint) = 32),
          requested_at timestamptz NOT NULL DEFAULT now(),
          decided_at timestamptz,
          decided_by_staff_user_id uuid,
          rejection_reason text CHECK (
            rejection_reason IS NULL
            OR rejection_reason IN ('availability_conflict', 'manual_review')
          ),
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (tenant_id, id),
          UNIQUE (tenant_id, idempotency_key_hash),
          FOREIGN KEY (tenant_id, order_id) REFERENCES orders(tenant_id, id) ON DELETE CASCADE,
          FOREIGN KEY (tenant_id, customer_id) REFERENCES customers(tenant_id, id),
          FOREIGN KEY (tenant_id, decided_by_staff_user_id)
            REFERENCES tenant_memberships(tenant_id, user_id),
          CHECK (requested_end_date > original_end_date),
          CHECK (
            (state = 'pending' AND decided_at IS NULL
              AND decided_by_staff_user_id IS NULL AND rejection_reason IS NULL)
            OR
            (state = 'approved' AND decided_at IS NOT NULL
              AND decided_by_staff_user_id IS NOT NULL AND rejection_reason IS NULL)
            OR
            (state = 'rejected' AND decided_at IS NOT NULL
              AND decided_by_staff_user_id IS NOT NULL AND rejection_reason IS NOT NULL)
          )
        );
        -- statement
        CREATE UNIQUE INDEX ux_order_extension_requests_pending_order
          ON order_extension_requests (tenant_id, order_id) WHERE state = 'pending';
        -- statement
        CREATE INDEX ix_order_extension_requests_tenant_state
          ON order_extension_requests (tenant_id, state, requested_at DESC);
        -- statement
        CREATE TABLE order_return_requests (
          id uuid PRIMARY KEY,
          tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
          order_id uuid NOT NULL,
          customer_id uuid NOT NULL,
          requested_date date NOT NULL,
          state text NOT NULL DEFAULT 'pending'
            CHECK (state IN ('pending', 'acknowledged', 'rejected')),
          idempotency_key_hash bytea NOT NULL CHECK (octet_length(idempotency_key_hash) = 32),
          command_fingerprint bytea NOT NULL CHECK (octet_length(command_fingerprint) = 32),
          requested_at timestamptz NOT NULL DEFAULT now(),
          decided_at timestamptz,
          decided_by_staff_user_id uuid,
          rejection_reason text CHECK (
            rejection_reason IS NULL OR rejection_reason IN ('date_unavailable', 'manual_review')
          ),
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (tenant_id, id),
          UNIQUE (tenant_id, idempotency_key_hash),
          FOREIGN KEY (tenant_id, order_id) REFERENCES orders(tenant_id, id) ON DELETE CASCADE,
          FOREIGN KEY (tenant_id, customer_id) REFERENCES customers(tenant_id, id),
          FOREIGN KEY (tenant_id, decided_by_staff_user_id)
            REFERENCES tenant_memberships(tenant_id, user_id),
          CHECK (
            (state = 'pending' AND decided_at IS NULL
              AND decided_by_staff_user_id IS NULL AND rejection_reason IS NULL)
            OR
            (state = 'acknowledged' AND decided_at IS NOT NULL
              AND decided_by_staff_user_id IS NOT NULL AND rejection_reason IS NULL)
            OR
            (state = 'rejected' AND decided_at IS NOT NULL
              AND decided_by_staff_user_id IS NOT NULL AND rejection_reason IS NOT NULL)
          )
        );
        -- statement
        CREATE UNIQUE INDEX ux_order_return_requests_pending_order
          ON order_return_requests (tenant_id, order_id) WHERE state = 'pending';
        -- statement
        CREATE INDEX ix_order_return_requests_tenant_state
          ON order_return_requests (tenant_id, state, requested_at DESC);
        -- statement
        CREATE TABLE notification_jobs (
          id uuid PRIMARY KEY,
          tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
          order_id uuid,
          customer_id uuid,
          channel text NOT NULL CHECK (channel = 'sms'),
          template text NOT NULL CHECK (template IN (
            'return_due_24h', 'return_overdue', 'extension_approved',
            'extension_rejected', 'return_acknowledged', 'return_rejected'
          )),
          send_after timestamptz NOT NULL,
          state text NOT NULL CHECK (state IN ('blocked', 'queued', 'sent', 'failed', 'cancelled')),
          blocked_reason text CHECK (
            blocked_reason IS NULL OR blocked_reason = 'provider_disabled'
          ),
          dedupe_key text NOT NULL CHECK (length(dedupe_key) BETWEEN 1 AND 200),
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (tenant_id, id),
          UNIQUE (tenant_id, dedupe_key),
          FOREIGN KEY (tenant_id, order_id) REFERENCES orders(tenant_id, id) ON DELETE CASCADE,
          FOREIGN KEY (tenant_id, customer_id) REFERENCES customers(tenant_id, id),
          CHECK (
            (state = 'blocked' AND blocked_reason = 'provider_disabled')
            OR (state <> 'blocked' AND blocked_reason IS NULL)
          )
        );
        -- statement
        CREATE INDEX ix_notification_jobs_tenant_state_send_after
          ON notification_jobs (tenant_id, state, send_after);
        -- statement
        ALTER TABLE order_extension_requests ENABLE ROW LEVEL SECURITY;
        -- statement
        ALTER TABLE order_extension_requests FORCE ROW LEVEL SECURITY;
        -- statement
        CREATE POLICY tenant_scope ON order_extension_requests
          USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        -- statement
        ALTER TABLE order_return_requests ENABLE ROW LEVEL SECURITY;
        -- statement
        ALTER TABLE order_return_requests FORCE ROW LEVEL SECURITY;
        -- statement
        CREATE POLICY tenant_scope ON order_return_requests
          USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        -- statement
        ALTER TABLE notification_jobs ENABLE ROW LEVEL SECURITY;
        -- statement
        ALTER TABLE notification_jobs FORCE ROW LEVEL SECURITY;
        -- statement
        CREATE POLICY tenant_scope ON notification_jobs
          USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        -- statement
        REVOKE ALL ON order_extension_requests FROM PUBLIC;
        -- statement
        REVOKE ALL ON order_return_requests FROM PUBLIC;
        -- statement
        REVOKE ALL ON notification_jobs FROM PUBLIC;
        -- statement
        GRANT SELECT, INSERT, UPDATE ON order_extension_requests TO photolamp_runtime;
        -- statement
        GRANT SELECT, INSERT, UPDATE ON order_return_requests TO photolamp_runtime;
        -- statement
        GRANT SELECT, INSERT, UPDATE ON notification_jobs TO photolamp_runtime;
        """
    )


def downgrade() -> None:
    execute_script(
        """
        DROP TABLE IF EXISTS notification_jobs;
        -- statement
        DROP TABLE IF EXISTS order_return_requests;
        -- statement
        DROP TABLE IF EXISTS order_extension_requests;
        -- statement
        ALTER TABLE order_events DROP CONSTRAINT order_events_event_type_check;
        -- statement
        DELETE FROM order_events WHERE event_type IN (
          'order_extension_requested', 'order_extension_approved', 'order_extension_rejected',
          'order_return_requested', 'order_return_acknowledged', 'order_return_rejected'
        );
        -- statement
        ALTER TABLE order_events ADD CONSTRAINT order_events_event_type_check CHECK (
          event_type IN (
            'draft_created', 'phone_order_created', 'order_confirmed', 'order_cancelled',
            'contact_accessed', 'order_handed_over', 'order_returned',
            'order_inspection_completed'
          )
        );
        -- statement
        ALTER TABLE order_events DROP CONSTRAINT order_events_single_actor_check;
        -- statement
        ALTER TABLE order_events DROP CONSTRAINT order_events_actor_customer_fk;
        -- statement
        ALTER TABLE order_events DROP COLUMN actor_customer_id;
        """
    )

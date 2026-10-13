"""Anonymous checkout capabilities and expiring draft orders.

Revision ID: 0007_checkout_draft_orders
Revises: 0006_atomic_rental_holds
"""

from alembic import op

revision = "0007_checkout_draft_orders"
down_revision = "0006_atomic_rental_holds"
branch_labels = None
depends_on = None


def execute_script(script: str) -> None:
    for statement in script.split("\n        -- statement\n"):
        op.execute(statement)


def upgrade() -> None:
    execute_script(
        """
        CREATE TABLE checkout_sessions (
          id uuid PRIMARY KEY,
          tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
          token_hash bytea NOT NULL CHECK (octet_length(token_hash) = 32),
          state text NOT NULL DEFAULT 'active'
            CHECK (state IN ('active', 'expired', 'revoked')),
          expires_at timestamptz NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (tenant_id, id),
          UNIQUE (tenant_id, token_hash)
        );
        -- statement
        CREATE INDEX ix_checkout_sessions_tenant_state_expiry
          ON checkout_sessions (tenant_id, state, expires_at);
        -- statement
        ALTER TABLE checkout_sessions ENABLE ROW LEVEL SECURITY;
        -- statement
        ALTER TABLE checkout_sessions FORCE ROW LEVEL SECURITY;
        -- statement
        CREATE POLICY tenant_scope ON checkout_sessions
          USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        -- statement
        REVOKE ALL ON checkout_sessions FROM PUBLIC;
        -- statement
        GRANT SELECT, INSERT, UPDATE ON checkout_sessions TO photolamp_runtime;
        -- statement
        ALTER TABLE rental_allocations ADD COLUMN checkout_session_id uuid;
        -- statement
        ALTER TABLE rental_allocations ADD CONSTRAINT rental_allocations_checkout_session_fk
          FOREIGN KEY (tenant_id, checkout_session_id)
          REFERENCES checkout_sessions(tenant_id, id);
        -- statement
        CREATE TABLE orders (
          id uuid PRIMARY KEY,
          tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
          branch_id uuid NOT NULL,
          checkout_session_id uuid NOT NULL,
          allocation_id uuid NOT NULL,
          source text NOT NULL CHECK (source = 'website'),
          state text NOT NULL CHECK (state IN ('held', 'expired', 'cancelled')),
          start_date date NOT NULL,
          end_date date NOT NULL,
          fulfillment text NOT NULL CHECK (fulfillment IN ('pickup', 'delivery')),
          expires_at timestamptz NOT NULL,
          quote_snapshot jsonb NOT NULL,
          idempotency_key_hash bytea NOT NULL
            CHECK (octet_length(idempotency_key_hash) = 32),
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (tenant_id, id),
          UNIQUE (tenant_id, allocation_id),
          UNIQUE (tenant_id, idempotency_key_hash),
          CHECK (end_date >= start_date),
          FOREIGN KEY (tenant_id, branch_id) REFERENCES branches(tenant_id, id),
          FOREIGN KEY (tenant_id, checkout_session_id)
            REFERENCES checkout_sessions(tenant_id, id),
          FOREIGN KEY (tenant_id, allocation_id)
            REFERENCES rental_allocations(tenant_id, id)
        );
        -- statement
        CREATE INDEX ix_orders_tenant_state_start ON orders (tenant_id, state, start_date);
        -- statement
        ALTER TABLE orders ENABLE ROW LEVEL SECURITY;
        -- statement
        ALTER TABLE orders FORCE ROW LEVEL SECURITY;
        -- statement
        CREATE POLICY tenant_scope ON orders
          USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        -- statement
        REVOKE ALL ON orders FROM PUBLIC;
        -- statement
        GRANT SELECT, INSERT, UPDATE ON orders TO photolamp_runtime;
        -- statement
        ALTER TABLE rental_allocations ADD COLUMN order_id uuid;
        -- statement
        ALTER TABLE rental_allocations ADD CONSTRAINT rental_allocations_order_fk
          FOREIGN KEY (tenant_id, order_id) REFERENCES orders(tenant_id, id);
        -- statement
        CREATE UNIQUE INDEX uq_rental_allocations_tenant_order
          ON rental_allocations (tenant_id, order_id) WHERE order_id IS NOT NULL;
        -- statement
        CREATE TABLE order_events (
          id uuid PRIMARY KEY,
          tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
          order_id uuid NOT NULL,
          event_type text NOT NULL CHECK (event_type = 'draft_created'),
          payload jsonb NOT NULL DEFAULT '{}',
          idempotency_key_hash bytea NOT NULL
            CHECK (octet_length(idempotency_key_hash) = 32),
          happened_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (tenant_id, id),
          UNIQUE (tenant_id, idempotency_key_hash),
          FOREIGN KEY (tenant_id, order_id) REFERENCES orders(tenant_id, id)
        );
        -- statement
        CREATE INDEX ix_order_events_tenant_order_time
          ON order_events (tenant_id, order_id, happened_at);
        -- statement
        ALTER TABLE order_events ENABLE ROW LEVEL SECURITY;
        -- statement
        ALTER TABLE order_events FORCE ROW LEVEL SECURITY;
        -- statement
        CREATE POLICY tenant_scope ON order_events
          USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        -- statement
        REVOKE ALL ON order_events FROM PUBLIC;
        -- statement
        GRANT SELECT, INSERT ON order_events TO photolamp_runtime;
        """
    )


def downgrade() -> None:
    execute_script(
        """
        DROP TABLE IF EXISTS order_events;
        -- statement
        DROP INDEX IF EXISTS uq_rental_allocations_tenant_order;
        -- statement
        ALTER TABLE rental_allocations DROP CONSTRAINT IF EXISTS rental_allocations_order_fk;
        -- statement
        ALTER TABLE rental_allocations DROP COLUMN IF EXISTS order_id;
        -- statement
        DROP TABLE IF EXISTS orders;
        -- statement
        ALTER TABLE rental_allocations
          DROP CONSTRAINT IF EXISTS rental_allocations_checkout_session_fk;
        -- statement
        ALTER TABLE rental_allocations DROP COLUMN IF EXISTS checkout_session_id;
        -- statement
        DROP TABLE IF EXISTS checkout_sessions;
        """
    )

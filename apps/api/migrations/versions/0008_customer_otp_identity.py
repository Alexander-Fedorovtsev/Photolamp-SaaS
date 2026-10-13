"""Privacy-minimized customer OTP identity and sessions.

Revision ID: 0008_customer_otp_identity
Revises: 0007_checkout_draft_orders
"""

from alembic import op

revision = "0008_customer_otp_identity"
down_revision = "0007_checkout_draft_orders"
branch_labels = None
depends_on = None


def execute_script(script: str) -> None:
    for statement in script.split("\n        -- statement\n"):
        op.execute(statement)


def upgrade() -> None:
    execute_script(
        """
        CREATE TABLE customers (
          id uuid PRIMARY KEY,
          tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
          phone_lookup_hash bytea NOT NULL CHECK (octet_length(phone_lookup_hash) = 32),
          phone_last4 text NOT NULL CHECK (char_length(phone_last4) = 4),
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (tenant_id, id),
          UNIQUE (tenant_id, phone_lookup_hash)
        );
        -- statement
        ALTER TABLE customers ENABLE ROW LEVEL SECURITY;
        -- statement
        ALTER TABLE customers FORCE ROW LEVEL SECURITY;
        -- statement
        CREATE POLICY tenant_scope ON customers
          USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        -- statement
        REVOKE ALL ON customers FROM PUBLIC;
        -- statement
        GRANT SELECT, INSERT, UPDATE ON customers TO photolamp_runtime;
        -- statement
        CREATE TABLE customer_sessions (
          id uuid PRIMARY KEY,
          tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
          customer_id uuid NOT NULL,
          token_hash bytea NOT NULL CHECK (octet_length(token_hash) = 32),
          state text NOT NULL DEFAULT 'active'
            CHECK (state IN ('active', 'expired', 'revoked')),
          expires_at timestamptz NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (tenant_id, id),
          UNIQUE (tenant_id, token_hash),
          FOREIGN KEY (tenant_id, customer_id) REFERENCES customers(tenant_id, id)
        );
        -- statement
        CREATE INDEX ix_customer_sessions_tenant_customer_state
          ON customer_sessions (tenant_id, customer_id, state, expires_at);
        -- statement
        ALTER TABLE customer_sessions ENABLE ROW LEVEL SECURITY;
        -- statement
        ALTER TABLE customer_sessions FORCE ROW LEVEL SECURITY;
        -- statement
        CREATE POLICY tenant_scope ON customer_sessions
          USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        -- statement
        REVOKE ALL ON customer_sessions FROM PUBLIC;
        -- statement
        GRANT SELECT, INSERT, UPDATE ON customer_sessions TO photolamp_runtime;
        -- statement
        CREATE TABLE customer_auth_challenges (
          id uuid PRIMARY KEY,
          tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
          checkout_session_id uuid NOT NULL,
          customer_session_id uuid,
          phone_lookup_hash bytea NOT NULL CHECK (octet_length(phone_lookup_hash) = 32),
          phone_last4 text NOT NULL CHECK (char_length(phone_last4) = 4),
          code_hash bytea NOT NULL CHECK (octet_length(code_hash) = 32),
          state text NOT NULL DEFAULT 'pending'
            CHECK (state IN ('pending', 'verified', 'expired', 'locked', 'superseded')),
          attempts_remaining integer NOT NULL CHECK (attempts_remaining BETWEEN 0 AND 10),
          expires_at timestamptz NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (tenant_id, id),
          FOREIGN KEY (tenant_id, checkout_session_id)
            REFERENCES checkout_sessions(tenant_id, id),
          FOREIGN KEY (tenant_id, customer_session_id)
            REFERENCES customer_sessions(tenant_id, id)
        );
        -- statement
        CREATE INDEX ix_customer_auth_challenges_tenant_checkout_state
          ON customer_auth_challenges (tenant_id, checkout_session_id, state, expires_at);
        -- statement
        ALTER TABLE customer_auth_challenges ENABLE ROW LEVEL SECURITY;
        -- statement
        ALTER TABLE customer_auth_challenges FORCE ROW LEVEL SECURITY;
        -- statement
        CREATE POLICY tenant_scope ON customer_auth_challenges
          USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        -- statement
        REVOKE ALL ON customer_auth_challenges FROM PUBLIC;
        -- statement
        GRANT SELECT, INSERT, UPDATE ON customer_auth_challenges TO photolamp_runtime;
        -- statement
        ALTER TABLE checkout_sessions ADD COLUMN customer_id uuid;
        -- statement
        ALTER TABLE checkout_sessions ADD CONSTRAINT checkout_sessions_customer_fk
          FOREIGN KEY (tenant_id, customer_id) REFERENCES customers(tenant_id, id);
        -- statement
        ALTER TABLE orders ADD COLUMN customer_id uuid;
        -- statement
        ALTER TABLE orders ADD CONSTRAINT orders_customer_fk
          FOREIGN KEY (tenant_id, customer_id) REFERENCES customers(tenant_id, id);
        -- statement
        CREATE INDEX ix_orders_tenant_customer_state
          ON orders (tenant_id, customer_id, state) WHERE customer_id IS NOT NULL;
        """
    )


def downgrade() -> None:
    execute_script(
        """
        DROP INDEX IF EXISTS ix_orders_tenant_customer_state;
        -- statement
        ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_customer_fk;
        -- statement
        ALTER TABLE orders DROP COLUMN IF EXISTS customer_id;
        -- statement
        ALTER TABLE checkout_sessions DROP CONSTRAINT IF EXISTS checkout_sessions_customer_fk;
        -- statement
        ALTER TABLE checkout_sessions DROP COLUMN IF EXISTS customer_id;
        -- statement
        DROP TABLE IF EXISTS customer_auth_challenges;
        -- statement
        DROP TABLE IF EXISTS customer_sessions;
        -- statement
        DROP TABLE IF EXISTS customers;
        """
    )

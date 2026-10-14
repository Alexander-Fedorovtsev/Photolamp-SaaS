"""Encrypted contacts and staff-created phone orders.

Revision ID: 0010_phone_orders
Revises: 0009_order_operations
"""

from alembic import op

revision = "0010_phone_orders"
down_revision = "0009_order_operations"
branch_labels = None
depends_on = None


def execute_script(script: str) -> None:
    for statement in script.split("\n        -- statement\n"):
        op.execute(statement)


def upgrade() -> None:
    execute_script(
        """
        ALTER TABLE customers ADD COLUMN phone_verified_at timestamptz;
        -- statement
        UPDATE customers SET phone_verified_at = created_at;
        -- statement
        CREATE TABLE customer_private_profiles (
          tenant_id uuid NOT NULL,
          customer_id uuid NOT NULL,
          ciphertext bytea NOT NULL,
          nonce bytea NOT NULL CHECK (octet_length(nonce) = 12),
          key_version smallint NOT NULL DEFAULT 1 CHECK (key_version > 0),
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (tenant_id, customer_id),
          FOREIGN KEY (tenant_id, customer_id)
            REFERENCES customers(tenant_id, id) ON DELETE CASCADE
        );
        -- statement
        ALTER TABLE customer_private_profiles ENABLE ROW LEVEL SECURITY;
        -- statement
        ALTER TABLE customer_private_profiles FORCE ROW LEVEL SECURITY;
        -- statement
        CREATE POLICY tenant_scope ON customer_private_profiles
          USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        -- statement
        REVOKE ALL ON customer_private_profiles FROM PUBLIC;
        -- statement
        GRANT SELECT, INSERT, UPDATE ON customer_private_profiles TO photolamp_runtime;
        -- statement
        ALTER TABLE orders DROP CONSTRAINT orders_source_check;
        -- statement
        ALTER TABLE orders ADD CONSTRAINT orders_source_check
          CHECK (source IN ('website', 'phone'));
        -- statement
        ALTER TABLE orders ALTER COLUMN checkout_session_id DROP NOT NULL;
        -- statement
        ALTER TABLE orders ALTER COLUMN expires_at DROP NOT NULL;
        -- statement
        ALTER TABLE orders ADD COLUMN created_by_staff_user_id uuid;
        -- statement
        ALTER TABLE orders ADD CONSTRAINT orders_created_by_membership_fk
          FOREIGN KEY (tenant_id, created_by_staff_user_id)
          REFERENCES tenant_memberships(tenant_id, user_id);
        -- statement
        ALTER TABLE orders ADD COLUMN command_fingerprint bytea
          CHECK (command_fingerprint IS NULL OR octet_length(command_fingerprint) = 32);
        -- statement
        ALTER TABLE orders ADD CONSTRAINT orders_source_shape_check CHECK (
          (source = 'website' AND checkout_session_id IS NOT NULL
            AND created_by_staff_user_id IS NULL)
          OR
          (source = 'phone' AND checkout_session_id IS NULL
            AND created_by_staff_user_id IS NOT NULL AND customer_id IS NOT NULL
            AND command_fingerprint IS NOT NULL)
        );
        -- statement
        CREATE TABLE order_private_details (
          tenant_id uuid NOT NULL,
          order_id uuid NOT NULL,
          ciphertext bytea NOT NULL,
          nonce bytea NOT NULL CHECK (octet_length(nonce) = 12),
          key_version smallint NOT NULL DEFAULT 1 CHECK (key_version > 0),
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (tenant_id, order_id),
          FOREIGN KEY (tenant_id, order_id)
            REFERENCES orders(tenant_id, id) ON DELETE CASCADE
        );
        -- statement
        ALTER TABLE order_private_details ENABLE ROW LEVEL SECURITY;
        -- statement
        ALTER TABLE order_private_details FORCE ROW LEVEL SECURITY;
        -- statement
        CREATE POLICY tenant_scope ON order_private_details
          USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        -- statement
        REVOKE ALL ON order_private_details FROM PUBLIC;
        -- statement
        GRANT SELECT, INSERT ON order_private_details TO photolamp_runtime;
        -- statement
        ALTER TABLE order_events DROP CONSTRAINT order_events_event_type_check;
        -- statement
        ALTER TABLE order_events ADD CONSTRAINT order_events_event_type_check
          CHECK (event_type IN (
            'draft_created', 'phone_order_created', 'order_confirmed', 'order_cancelled'
          ));
        """
    )


def downgrade() -> None:
    execute_script(
        """
        ALTER TABLE order_events DROP CONSTRAINT order_events_event_type_check;
        -- statement
        DELETE FROM order_events
          WHERE order_id IN (SELECT id FROM orders WHERE source = 'phone');
        -- statement
        ALTER TABLE order_events ADD CONSTRAINT order_events_event_type_check
          CHECK (event_type IN ('draft_created', 'order_confirmed', 'order_cancelled'));
        -- statement
        DROP TABLE IF EXISTS order_private_details;
        -- statement
        UPDATE rental_allocations SET state = 'cancelled', order_id = NULL, updated_at = now()
          WHERE order_id IN (SELECT id FROM orders WHERE source = 'phone');
        -- statement
        DELETE FROM orders WHERE source = 'phone';
        -- statement
        ALTER TABLE orders DROP CONSTRAINT orders_source_shape_check;
        -- statement
        ALTER TABLE orders DROP COLUMN command_fingerprint;
        -- statement
        ALTER TABLE orders DROP CONSTRAINT orders_created_by_membership_fk;
        -- statement
        ALTER TABLE orders DROP COLUMN created_by_staff_user_id;
        -- statement
        ALTER TABLE orders ALTER COLUMN expires_at SET NOT NULL;
        -- statement
        ALTER TABLE orders ALTER COLUMN checkout_session_id SET NOT NULL;
        -- statement
        ALTER TABLE orders DROP CONSTRAINT orders_source_check;
        -- statement
        ALTER TABLE orders ADD CONSTRAINT orders_source_check CHECK (source = 'website');
        -- statement
        DROP TABLE IF EXISTS customer_private_profiles;
        -- statement
        ALTER TABLE customers DROP COLUMN phone_verified_at;
        """
    )

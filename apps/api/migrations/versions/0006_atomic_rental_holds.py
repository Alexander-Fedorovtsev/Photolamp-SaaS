"""Atomic, expiring allocations for the public rental flow.

Revision ID: 0006_atomic_rental_holds
Revises: 0005_availability_quote
"""

from alembic import op

revision = "0006_atomic_rental_holds"
down_revision = "0005_availability_quote"
branch_labels = None
depends_on = None


def execute_script(script: str) -> None:
    for statement in script.split("\n        -- statement\n"):
        op.execute(statement)


def upgrade() -> None:
    execute_script(
        """
        CREATE EXTENSION IF NOT EXISTS btree_gist;
        -- statement
        CREATE TABLE rental_allocations (
          id uuid PRIMARY KEY,
          tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
          asset_id uuid NOT NULL,
          equipment_model_id uuid NOT NULL,
          branch_id uuid NOT NULL,
          start_date date NOT NULL,
          end_date date NOT NULL,
          fulfillment text NOT NULL CHECK (fulfillment IN ('pickup', 'delivery')),
          state text NOT NULL
            CHECK (state IN ('held', 'confirmed', 'released', 'expired', 'cancelled')),
          expires_at timestamptz,
          idempotency_key_hash bytea NOT NULL CHECK (octet_length(idempotency_key_hash) = 32),
          management_token_hash bytea NOT NULL CHECK (octet_length(management_token_hash) = 32),
          quote_snapshot jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (tenant_id, id),
          UNIQUE (tenant_id, idempotency_key_hash),
          CHECK (end_date >= start_date),
          CHECK (state <> 'held' OR expires_at IS NOT NULL),
          FOREIGN KEY (tenant_id, asset_id) REFERENCES assets(tenant_id, id),
          FOREIGN KEY (tenant_id, equipment_model_id)
            REFERENCES equipment_models(tenant_id, id),
          FOREIGN KEY (tenant_id, branch_id) REFERENCES branches(tenant_id, id)
        );
        -- statement
        ALTER TABLE rental_allocations ADD CONSTRAINT rental_allocations_no_overlap
          EXCLUDE USING gist (
            tenant_id WITH =,
            asset_id WITH =,
            daterange(start_date, end_date, '[]') WITH &&
          ) WHERE (state IN ('held', 'confirmed'));
        -- statement
        CREATE INDEX ix_rental_allocations_tenant_state_expiry
          ON rental_allocations (tenant_id, state, expires_at);
        -- statement
        ALTER TABLE rental_allocations ENABLE ROW LEVEL SECURITY;
        -- statement
        ALTER TABLE rental_allocations FORCE ROW LEVEL SECURITY;
        -- statement
        CREATE POLICY tenant_scope ON rental_allocations
          USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        -- statement
        REVOKE ALL ON rental_allocations FROM PUBLIC;
        -- statement
        GRANT SELECT, INSERT, UPDATE ON rental_allocations TO photolamp_runtime;
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS rental_allocations")

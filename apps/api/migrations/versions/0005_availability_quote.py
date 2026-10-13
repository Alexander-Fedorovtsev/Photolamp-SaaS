"""Date-aware availability blocks for public quotes.

Revision ID: 0005_availability_quote
Revises: 0004_staff_catalog_admin
"""

from alembic import op

revision = "0005_availability_quote"
down_revision = "0004_staff_catalog_admin"
branch_labels = None
depends_on = None


def execute_script(script: str) -> None:
    for statement in script.split("\n        -- statement\n"):
        op.execute(statement)


def upgrade() -> None:
    execute_script(
        """
        CREATE TABLE asset_blocks (
          id uuid PRIMARY KEY,
          tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
          asset_id uuid NOT NULL,
          start_date date NOT NULL,
          end_date date NOT NULL,
          reason text NOT NULL CHECK (reason IN ('maintenance', 'inspection', 'manual')),
          note text CHECK (note IS NULL OR length(note) BETWEEN 1 AND 240),
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (tenant_id, id),
          CHECK (end_date >= start_date),
          FOREIGN KEY (tenant_id, asset_id) REFERENCES assets(tenant_id, id) ON DELETE CASCADE
        );
        -- statement
        CREATE INDEX ix_asset_blocks_tenant_asset_dates
          ON asset_blocks (tenant_id, asset_id, start_date, end_date);
        -- statement
        ALTER TABLE asset_blocks ENABLE ROW LEVEL SECURITY;
        -- statement
        ALTER TABLE asset_blocks FORCE ROW LEVEL SECURITY;
        -- statement
        CREATE POLICY tenant_scope ON asset_blocks
          USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        -- statement
        REVOKE ALL ON asset_blocks FROM PUBLIC;
        -- statement
        GRANT SELECT, INSERT, DELETE ON asset_blocks TO photolamp_runtime;
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS asset_blocks")

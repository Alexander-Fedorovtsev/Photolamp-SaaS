"""Durable ledger for contact-key rotation maintenance.

Revision ID: 0016_contact_key_rotation
Revises: 0015_staff_session_management
"""

from alembic import op

revision = "0016_contact_key_rotation"
down_revision = "0015_staff_session_management"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE contact_key_rotation_runs (
          id uuid PRIMARY KEY,
          scope_tenant_id uuid REFERENCES tenants(id) ON DELETE SET NULL,
          target_key_version smallint NOT NULL CHECK (target_key_version > 0),
          state text NOT NULL CHECK (state IN ('running', 'succeeded', 'failed')),
          customer_profiles_rotated bigint NOT NULL DEFAULT 0
            CHECK (customer_profiles_rotated >= 0),
          order_details_rotated bigint NOT NULL DEFAULT 0
            CHECK (order_details_rotated >= 0),
          failure_code text CHECK (failure_code IS NULL OR length(failure_code) <= 80),
          started_at timestamptz NOT NULL DEFAULT now(),
          finished_at timestamptz,
          CHECK ((state = 'running' AND finished_at IS NULL AND failure_code IS NULL)
            OR (state = 'succeeded' AND finished_at IS NOT NULL AND failure_code IS NULL)
            OR (state = 'failed' AND finished_at IS NOT NULL AND failure_code IS NOT NULL))
        )
        """
    )
    op.execute("REVOKE ALL ON contact_key_rotation_runs FROM PUBLIC")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS contact_key_rotation_runs")

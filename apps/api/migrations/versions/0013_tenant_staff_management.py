"""Tenant staff directory, role controls and synthetic demo team.

Revision ID: 0013_tenant_staff_management
Revises: 0012_order_requests
"""

from alembic import op

revision = "0013_tenant_staff_management"
down_revision = "0012_order_requests"
branch_labels = None
depends_on = None


def execute_script(script: str) -> None:
    for statement in script.split("\n        -- statement\n"):
        op.execute(statement)


def upgrade() -> None:
    execute_script(
        """
        ALTER TABLE tenant_memberships
          ADD COLUMN display_name text NOT NULL DEFAULT 'Сотрудник';
        -- statement
        ALTER TABLE tenant_memberships
          ADD CONSTRAINT tenant_memberships_display_name_check
          CHECK (length(display_name) BETWEEN 1 AND 120);
        -- statement
        ALTER TABLE tenant_memberships
          ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now();
        -- statement
        CREATE INDEX ix_tenant_memberships_tenant_state_role
          ON tenant_memberships (tenant_id, state, role);

        -- statement
        UPDATE tenant_memberships SET display_name = 'Демо-владелец'
          WHERE user_id = 'd5000000-0000-0000-0000-000000000001'
            AND tenant_id IN (
              'd0000000-0000-0000-0000-000000000001',
              'd0000000-0000-0000-0000-000000000002'
            );
        -- statement
        INSERT INTO users (id, phone_e164) VALUES
          ('d5000000-0000-0000-0000-000000000002', '+19990009998'),
          ('d5000000-0000-0000-0000-000000000003', '+19990009997'),
          ('d5000000-0000-0000-0000-000000000004', '+19990009996')
        ON CONFLICT DO NOTHING;
        -- statement
        INSERT INTO tenant_memberships
          (tenant_id, user_id, role, state, display_name) VALUES
          ('d0000000-0000-0000-0000-000000000001',
           'd5000000-0000-0000-0000-000000000002',
           'operator', 'active', 'Демо-оператор'),
          ('d0000000-0000-0000-0000-000000000001',
           'd5000000-0000-0000-0000-000000000003',
           'courier', 'active', 'Демо-курьер'),
          ('d0000000-0000-0000-0000-000000000002',
           'd5000000-0000-0000-0000-000000000004',
           'admin', 'active', 'Демо-администратор')
        ON CONFLICT (tenant_id, user_id) DO NOTHING;
        """
    )


def downgrade() -> None:
    execute_script(
        """
        DELETE FROM tenant_memberships WHERE user_id IN (
          'd5000000-0000-0000-0000-000000000002',
          'd5000000-0000-0000-0000-000000000003',
          'd5000000-0000-0000-0000-000000000004'
        );
        -- statement
        DELETE FROM users WHERE id IN (
          'd5000000-0000-0000-0000-000000000002',
          'd5000000-0000-0000-0000-000000000003',
          'd5000000-0000-0000-0000-000000000004'
        );
        -- statement
        DROP INDEX IF EXISTS ix_tenant_memberships_tenant_state_role;
        -- statement
        ALTER TABLE tenant_memberships DROP COLUMN IF EXISTS updated_at;
        -- statement
        ALTER TABLE tenant_memberships
          DROP CONSTRAINT IF EXISTS tenant_memberships_display_name_check;
        -- statement
        ALTER TABLE tenant_memberships DROP COLUMN IF EXISTS display_name;
        """
    )

"""Tenant-scoped courier assignments and delivery task lifecycle.

Revision ID: 0017_courier_tasks
Revises: 0016_contact_key_rotation
"""

from alembic import op

revision = "0017_courier_tasks"
down_revision = "0016_contact_key_rotation"
branch_labels = None
depends_on = None


def execute_script(script: str) -> None:
    for statement in script.split("\n        -- statement\n"):
        op.execute(statement)


def upgrade() -> None:
    execute_script(
        """
        CREATE TABLE courier_tasks (
          id uuid PRIMARY KEY,
          tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
          order_id uuid NOT NULL,
          courier_user_id uuid NOT NULL,
          assigned_by_staff_user_id uuid NOT NULL,
          state text NOT NULL CHECK (
            state IN ('assigned', 'accepted', 'picked_up', 'delivered', 'cancelled')
          ),
          assigned_at timestamptz NOT NULL DEFAULT now(),
          accepted_at timestamptz,
          picked_up_at timestamptz,
          delivered_at timestamptz,
          updated_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (tenant_id, id),
          UNIQUE (tenant_id, order_id),
          FOREIGN KEY (tenant_id, order_id) REFERENCES orders(tenant_id, id)
            ON DELETE CASCADE,
          FOREIGN KEY (tenant_id, courier_user_id)
            REFERENCES tenant_memberships(tenant_id, user_id),
          FOREIGN KEY (tenant_id, assigned_by_staff_user_id)
            REFERENCES tenant_memberships(tenant_id, user_id),
          CHECK (
            (state = 'assigned' AND accepted_at IS NULL AND picked_up_at IS NULL
              AND delivered_at IS NULL)
            OR (state = 'accepted' AND accepted_at IS NOT NULL AND picked_up_at IS NULL
              AND delivered_at IS NULL)
            OR (state = 'picked_up' AND accepted_at IS NOT NULL AND picked_up_at IS NOT NULL
              AND delivered_at IS NULL)
            OR (state = 'delivered' AND accepted_at IS NOT NULL AND picked_up_at IS NOT NULL
              AND delivered_at IS NOT NULL)
            OR state = 'cancelled'
          )
        );
        -- statement
        CREATE INDEX ix_courier_tasks_tenant_courier_state
          ON courier_tasks (tenant_id, courier_user_id, state, assigned_at);
        -- statement
        ALTER TABLE courier_tasks ENABLE ROW LEVEL SECURITY;
        -- statement
        ALTER TABLE courier_tasks FORCE ROW LEVEL SECURITY;
        -- statement
        CREATE POLICY tenant_scope ON courier_tasks
          USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        -- statement
        REVOKE ALL ON courier_tasks FROM PUBLIC;
        -- statement
        GRANT SELECT, INSERT, UPDATE, DELETE ON courier_tasks TO photolamp_runtime;

        -- statement
        ALTER TABLE order_events DROP CONSTRAINT order_events_event_type_check;
        -- statement
        ALTER TABLE order_events ADD CONSTRAINT order_events_event_type_check CHECK (
          event_type IN (
            'draft_created', 'phone_order_created', 'order_confirmed', 'order_cancelled',
            'contact_accessed', 'order_handed_over', 'order_returned',
            'order_inspection_completed', 'order_extension_requested',
            'order_extension_approved', 'order_extension_rejected',
            'order_return_requested', 'order_return_acknowledged', 'order_return_rejected',
            'courier_assigned', 'courier_reassigned', 'courier_unassigned',
            'courier_accepted', 'courier_picked_up', 'courier_delivered',
            'courier_contact_accessed'
          )
        );
        """
    )


def downgrade() -> None:
    execute_script(
        """
        ALTER TABLE order_events DROP CONSTRAINT order_events_event_type_check;
        -- statement
        DELETE FROM order_events WHERE event_type IN (
          'courier_assigned', 'courier_reassigned', 'courier_unassigned',
          'courier_accepted', 'courier_picked_up', 'courier_delivered',
          'courier_contact_accessed'
        );
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
        DROP TABLE IF EXISTS courier_tasks;
        """
    )

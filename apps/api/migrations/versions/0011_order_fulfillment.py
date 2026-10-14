"""Order handover, return, inspection and audited contact access.

Revision ID: 0011_order_fulfillment
Revises: 0010_phone_orders
"""

from alembic import op

revision = "0011_order_fulfillment"
down_revision = "0010_phone_orders"
branch_labels = None
depends_on = None


def execute_script(script: str) -> None:
    for statement in script.split("\n        -- statement\n"):
        op.execute(statement)


def upgrade() -> None:
    execute_script(
        """
        ALTER TABLE orders DROP CONSTRAINT orders_confirmation_check;
        -- statement
        ALTER TABLE orders DROP CONSTRAINT orders_state_check;
        -- statement
        ALTER TABLE orders ADD CONSTRAINT orders_state_check CHECK (
          state IN (
            'held', 'confirmed', 'in_progress', 'returned', 'completed',
            'expired', 'cancelled'
          )
        );
        -- statement
        ALTER TABLE orders ADD COLUMN handed_over_at timestamptz;
        -- statement
        ALTER TABLE orders ADD COLUMN handed_over_by_staff_user_id uuid;
        -- statement
        ALTER TABLE orders ADD COLUMN returned_at timestamptz;
        -- statement
        ALTER TABLE orders ADD COLUMN returned_by_staff_user_id uuid;
        -- statement
        ALTER TABLE orders ADD COLUMN completed_at timestamptz;
        -- statement
        ALTER TABLE orders ADD COLUMN completed_by_staff_user_id uuid;
        -- statement
        ALTER TABLE orders ADD COLUMN inspection_outcome text;
        -- statement
        ALTER TABLE orders ADD CONSTRAINT orders_handed_over_by_membership_fk
          FOREIGN KEY (tenant_id, handed_over_by_staff_user_id)
          REFERENCES tenant_memberships(tenant_id, user_id);
        -- statement
        ALTER TABLE orders ADD CONSTRAINT orders_returned_by_membership_fk
          FOREIGN KEY (tenant_id, returned_by_staff_user_id)
          REFERENCES tenant_memberships(tenant_id, user_id);
        -- statement
        ALTER TABLE orders ADD CONSTRAINT orders_completed_by_membership_fk
          FOREIGN KEY (tenant_id, completed_by_staff_user_id)
          REFERENCES tenant_memberships(tenant_id, user_id);
        -- statement
        ALTER TABLE orders ADD CONSTRAINT orders_confirmation_check CHECK (
          (state IN ('confirmed', 'in_progress', 'returned', 'completed')
            AND confirmed_at IS NOT NULL AND confirmed_by_staff_user_id IS NOT NULL)
          OR state NOT IN ('confirmed', 'in_progress', 'returned', 'completed')
        );
        -- statement
        ALTER TABLE orders ADD CONSTRAINT orders_fulfillment_lifecycle_check CHECK (
          (state IN ('held', 'confirmed', 'expired', 'cancelled')
            AND handed_over_at IS NULL AND handed_over_by_staff_user_id IS NULL
            AND returned_at IS NULL AND returned_by_staff_user_id IS NULL
            AND completed_at IS NULL AND completed_by_staff_user_id IS NULL
            AND inspection_outcome IS NULL)
          OR
          (state = 'in_progress'
            AND handed_over_at IS NOT NULL AND handed_over_by_staff_user_id IS NOT NULL
            AND returned_at IS NULL AND returned_by_staff_user_id IS NULL
            AND completed_at IS NULL AND completed_by_staff_user_id IS NULL
            AND inspection_outcome IS NULL)
          OR
          (state = 'returned'
            AND handed_over_at IS NOT NULL AND handed_over_by_staff_user_id IS NOT NULL
            AND returned_at IS NOT NULL AND returned_by_staff_user_id IS NOT NULL
            AND completed_at IS NULL AND completed_by_staff_user_id IS NULL
            AND inspection_outcome IS NULL)
          OR
          (state = 'completed'
            AND handed_over_at IS NOT NULL AND handed_over_by_staff_user_id IS NOT NULL
            AND returned_at IS NOT NULL AND returned_by_staff_user_id IS NOT NULL
            AND completed_at IS NOT NULL AND completed_by_staff_user_id IS NOT NULL
            AND inspection_outcome IN ('passed', 'maintenance_required'))
        );
        -- statement
        ALTER TABLE order_events DROP CONSTRAINT order_events_event_type_check;
        -- statement
        ALTER TABLE order_events ADD CONSTRAINT order_events_event_type_check CHECK (
          event_type IN (
            'draft_created', 'phone_order_created', 'order_confirmed', 'order_cancelled',
            'contact_accessed', 'order_handed_over', 'order_returned',
            'order_inspection_completed'
          )
        );
        """
    )


def downgrade() -> None:
    execute_script(
        """
        UPDATE assets a SET state = 'available', updated_at = now()
          FROM rental_allocations ra, orders o
          WHERE o.tenant_id = a.tenant_id AND o.allocation_id = ra.id
            AND ra.tenant_id = a.tenant_id AND ra.asset_id = a.id
            AND o.state IN ('in_progress', 'returned')
            AND a.state IN ('rented', 'inspection');
        -- statement
        UPDATE rental_allocations SET state = 'cancelled', updated_at = now()
          WHERE order_id IN (
            SELECT id FROM orders WHERE state IN ('in_progress', 'returned', 'completed')
          ) AND state = 'confirmed';
        -- statement
        UPDATE orders SET state = 'cancelled', handed_over_at = NULL,
          handed_over_by_staff_user_id = NULL, returned_at = NULL,
          returned_by_staff_user_id = NULL, completed_at = NULL,
          completed_by_staff_user_id = NULL, inspection_outcome = NULL,
          updated_at = now()
          WHERE state IN ('in_progress', 'returned', 'completed');
        -- statement
        ALTER TABLE order_events DROP CONSTRAINT order_events_event_type_check;
        -- statement
        DELETE FROM order_events WHERE event_type IN (
          'contact_accessed', 'order_handed_over', 'order_returned',
          'order_inspection_completed'
        );
        -- statement
        ALTER TABLE order_events ADD CONSTRAINT order_events_event_type_check CHECK (
          event_type IN (
            'draft_created', 'phone_order_created', 'order_confirmed', 'order_cancelled'
          )
        );
        -- statement
        ALTER TABLE orders DROP CONSTRAINT orders_fulfillment_lifecycle_check;
        -- statement
        ALTER TABLE orders DROP CONSTRAINT orders_confirmation_check;
        -- statement
        ALTER TABLE orders DROP CONSTRAINT orders_completed_by_membership_fk;
        -- statement
        ALTER TABLE orders DROP CONSTRAINT orders_returned_by_membership_fk;
        -- statement
        ALTER TABLE orders DROP CONSTRAINT orders_handed_over_by_membership_fk;
        -- statement
        ALTER TABLE orders DROP COLUMN inspection_outcome;
        -- statement
        ALTER TABLE orders DROP COLUMN completed_by_staff_user_id;
        -- statement
        ALTER TABLE orders DROP COLUMN completed_at;
        -- statement
        ALTER TABLE orders DROP COLUMN returned_by_staff_user_id;
        -- statement
        ALTER TABLE orders DROP COLUMN returned_at;
        -- statement
        ALTER TABLE orders DROP COLUMN handed_over_by_staff_user_id;
        -- statement
        ALTER TABLE orders DROP COLUMN handed_over_at;
        -- statement
        ALTER TABLE orders DROP CONSTRAINT orders_state_check;
        -- statement
        ALTER TABLE orders ADD CONSTRAINT orders_state_check
          CHECK (state IN ('held', 'confirmed', 'expired', 'cancelled'));
        -- statement
        ALTER TABLE orders ADD CONSTRAINT orders_confirmation_check CHECK (
          (state = 'confirmed' AND confirmed_at IS NOT NULL
            AND confirmed_by_staff_user_id IS NOT NULL)
          OR state <> 'confirmed'
        );
        """
    )

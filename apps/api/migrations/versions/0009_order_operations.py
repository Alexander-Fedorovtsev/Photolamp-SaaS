"""Immutable order snapshots and staff-controlled order transitions.

Revision ID: 0009_order_operations
Revises: 0008_customer_otp_identity
"""

from alembic import op

revision = "0009_order_operations"
down_revision = "0008_customer_otp_identity"
branch_labels = None
depends_on = None


def execute_script(script: str) -> None:
    for statement in script.split("\n        -- statement\n"):
        op.execute(statement)


def upgrade() -> None:
    execute_script(
        """
        ALTER TABLE orders ADD COLUMN order_snapshot jsonb;
        -- statement
        UPDATE orders o SET order_snapshot = jsonb_build_object(
          'schema_version', 1,
          'model_id', ra.equipment_model_id,
          'model_name', em.name,
          'branch_id', o.branch_id,
          'branch_name', b.name,
          'start_date', o.start_date,
          'end_date', o.end_date,
          'fulfillment', o.fulfillment,
          'quote', o.quote_snapshot
        )
        FROM rental_allocations ra, equipment_models em, branches b
        WHERE ra.tenant_id = o.tenant_id AND ra.id = o.allocation_id
          AND em.tenant_id = ra.tenant_id AND em.id = ra.equipment_model_id
          AND b.tenant_id = o.tenant_id AND b.id = o.branch_id;
        -- statement
        ALTER TABLE orders ALTER COLUMN order_snapshot SET NOT NULL;
        -- statement
        ALTER TABLE orders ADD CONSTRAINT orders_snapshot_schema_check CHECK (
          jsonb_typeof(order_snapshot) = 'object'
          AND order_snapshot @> '{"schema_version": 1}'::jsonb
        );
        -- statement
        CREATE FUNCTION prevent_order_snapshot_update()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $function$
        BEGIN
          IF NEW.order_snapshot IS DISTINCT FROM OLD.order_snapshot THEN
            RAISE EXCEPTION 'order_snapshot is immutable' USING ERRCODE = 'check_violation';
          END IF;
          RETURN NEW;
        END
        $function$;
        -- statement
        CREATE TRIGGER orders_immutable_snapshot
          BEFORE UPDATE OF order_snapshot ON orders
          FOR EACH ROW EXECUTE FUNCTION prevent_order_snapshot_update();
        -- statement
        ALTER TABLE orders ADD COLUMN confirmed_at timestamptz;
        -- statement
        ALTER TABLE orders ADD COLUMN confirmed_by_staff_user_id uuid;
        -- statement
        ALTER TABLE orders ADD CONSTRAINT orders_confirmed_by_membership_fk
          FOREIGN KEY (tenant_id, confirmed_by_staff_user_id)
          REFERENCES tenant_memberships(tenant_id, user_id);
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
        -- statement
        ALTER TABLE order_events ADD COLUMN actor_staff_user_id uuid;
        -- statement
        ALTER TABLE order_events ADD CONSTRAINT order_events_actor_membership_fk
          FOREIGN KEY (tenant_id, actor_staff_user_id)
          REFERENCES tenant_memberships(tenant_id, user_id);
        -- statement
        ALTER TABLE order_events DROP CONSTRAINT order_events_event_type_check;
        -- statement
        ALTER TABLE order_events ADD CONSTRAINT order_events_event_type_check
          CHECK (event_type IN ('draft_created', 'order_confirmed', 'order_cancelled'));
        """
    )


def downgrade() -> None:
    execute_script(
        """
        DELETE FROM order_events WHERE event_type IN ('order_confirmed', 'order_cancelled');
        -- statement
        UPDATE rental_allocations SET state = 'cancelled', updated_at = now()
          WHERE order_id IN (SELECT id FROM orders WHERE state = 'confirmed');
        -- statement
        UPDATE orders SET state = 'cancelled', updated_at = now() WHERE state = 'confirmed';
        -- statement
        ALTER TABLE order_events DROP CONSTRAINT order_events_event_type_check;
        -- statement
        ALTER TABLE order_events ADD CONSTRAINT order_events_event_type_check
          CHECK (event_type = 'draft_created');
        -- statement
        ALTER TABLE order_events DROP CONSTRAINT order_events_actor_membership_fk;
        -- statement
        ALTER TABLE order_events DROP COLUMN actor_staff_user_id;
        -- statement
        ALTER TABLE orders DROP CONSTRAINT orders_confirmation_check;
        -- statement
        ALTER TABLE orders DROP CONSTRAINT orders_state_check;
        -- statement
        ALTER TABLE orders ADD CONSTRAINT orders_state_check
          CHECK (state IN ('held', 'expired', 'cancelled'));
        -- statement
        ALTER TABLE orders DROP CONSTRAINT orders_confirmed_by_membership_fk;
        -- statement
        ALTER TABLE orders DROP COLUMN confirmed_by_staff_user_id;
        -- statement
        ALTER TABLE orders DROP COLUMN confirmed_at;
        -- statement
        ALTER TABLE orders DROP CONSTRAINT orders_snapshot_schema_check;
        -- statement
        DROP TRIGGER IF EXISTS orders_immutable_snapshot ON orders;
        -- statement
        DROP FUNCTION IF EXISTS prevent_order_snapshot_update();
        -- statement
        ALTER TABLE orders DROP COLUMN order_snapshot;
        """
    )

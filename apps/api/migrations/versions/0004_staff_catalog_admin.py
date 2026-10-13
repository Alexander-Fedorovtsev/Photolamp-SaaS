"""Demo staff sessions and auditable tenant catalog administration.

Revision ID: 0004_staff_catalog_admin
Revises: 0003_catalog_inventory
"""

from alembic import op

revision = "0004_staff_catalog_admin"
down_revision = "0003_catalog_inventory"
branch_labels = None
depends_on = None


def execute_script(script: str) -> None:
    for statement in script.split("\n        -- statement\n"):
        op.execute(statement)


def upgrade() -> None:
    execute_script(
        """
        CREATE TABLE audit_events (
          id uuid PRIMARY KEY,
          tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
          actor_user_id uuid NOT NULL REFERENCES users(id),
          action text NOT NULL CHECK (length(action) BETWEEN 3 AND 80),
          entity_type text NOT NULL CHECK (length(entity_type) BETWEEN 2 AND 60),
          entity_id uuid NOT NULL,
          changes jsonb NOT NULL DEFAULT '{}'::jsonb,
          occurred_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (tenant_id, id)
        );
        -- statement
        CREATE INDEX ix_audit_events_tenant_occurred
          ON audit_events (tenant_id, occurred_at DESC);
        -- statement
        ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY;
        -- statement
        ALTER TABLE audit_events FORCE ROW LEVEL SECURITY;
        -- statement
        CREATE POLICY tenant_scope ON audit_events
          USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);

        -- statement
        INSERT INTO users (id, phone_e164)
        VALUES ('d5000000-0000-0000-0000-000000000001', '+19990009999')
        ON CONFLICT (id) DO NOTHING;
        -- statement
        INSERT INTO tenant_memberships (tenant_id, user_id, role, state) VALUES
          ('d0000000-0000-0000-0000-000000000001',
           'd5000000-0000-0000-0000-000000000001', 'owner', 'active'),
          ('d0000000-0000-0000-0000-000000000002',
           'd5000000-0000-0000-0000-000000000001', 'owner', 'active')
        ON CONFLICT (tenant_id, user_id) DO NOTHING;

        -- statement
        CREATE FUNCTION issue_demo_staff_session(
          p_session_id uuid, p_token_hash bytea, p_expires_at timestamptz
        ) RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER VOLATILE
        SET search_path = pg_catalog
        AS $function$
        DECLARE
          demo_user constant uuid := 'd5000000-0000-0000-0000-000000000001';
        BEGIN
          IF octet_length(p_token_hash) <> 32
             OR p_expires_at <= statement_timestamp()
             OR p_expires_at > statement_timestamp() + interval '8 hours' THEN
            RAISE EXCEPTION 'invalid demo session request';
          END IF;
          IF NOT EXISTS (
            SELECT 1
            FROM public.tenant_memberships m
            JOIN public.storefront_profiles p ON p.tenant_id = m.tenant_id
            WHERE m.user_id = demo_user AND m.state = 'active'
              AND p.is_demo = true AND p.is_published = true
          ) THEN
            RAISE EXCEPTION 'demo staff identity unavailable';
          END IF;
          INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at)
          VALUES (p_session_id, demo_user, p_token_hash, p_expires_at);
          RETURN demo_user;
        END
        $function$;
        -- statement
        CREATE FUNCTION revoke_session(p_token_hash bytea)
        RETURNS boolean
        LANGUAGE sql SECURITY DEFINER VOLATILE
        SET search_path = pg_catalog
        AS $function$
          UPDATE public.auth_sessions
          SET revoked_at = statement_timestamp()
          WHERE token_hash = p_token_hash AND revoked_at IS NULL
          RETURNING true
        $function$;

        -- statement
        REVOKE ALL ON audit_events FROM PUBLIC;
        -- statement
        REVOKE ALL ON FUNCTION issue_demo_staff_session(uuid, bytea, timestamptz) FROM PUBLIC;
        -- statement
        REVOKE ALL ON FUNCTION revoke_session(bytea) FROM PUBLIC;
        -- statement
        GRANT SELECT, INSERT ON audit_events TO photolamp_runtime;
        -- statement
        GRANT EXECUTE ON FUNCTION issue_demo_staff_session(uuid, bytea, timestamptz)
          TO photolamp_runtime;
        -- statement
        GRANT EXECUTE ON FUNCTION revoke_session(bytea) TO photolamp_runtime;
        """
    )


def downgrade() -> None:
    execute_script(
        """
        DROP FUNCTION IF EXISTS revoke_session(bytea);
        -- statement
        DROP FUNCTION IF EXISTS issue_demo_staff_session(uuid, bytea, timestamptz);
        -- statement
        DELETE FROM auth_sessions
          WHERE user_id = 'd5000000-0000-0000-0000-000000000001';
        -- statement
        DELETE FROM tenant_memberships
          WHERE user_id = 'd5000000-0000-0000-0000-000000000001';
        -- statement
        DROP TABLE IF EXISTS audit_events;
        -- statement
        DELETE FROM users WHERE id = 'd5000000-0000-0000-0000-000000000001';
        """
    )

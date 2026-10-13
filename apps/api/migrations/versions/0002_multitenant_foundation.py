"""Tenant identities, memberships, opaque sessions and deny-by-default RLS.

Revision ID: 0002_multitenant_foundation
Revises: 0001_foundation
"""

from alembic import op

revision = "0002_multitenant_foundation"
down_revision = "0001_foundation"
branch_labels = None
depends_on = None


def execute_script(script: str) -> None:
    for statement in script.split("\n        -- statement\n"):
        op.execute(statement)


def upgrade() -> None:
    execute_script(
        """
        CREATE TABLE users (
          id uuid PRIMARY KEY,
          phone_e164 text NOT NULL UNIQUE CHECK (phone_e164 ~ '^\\+[1-9][0-9]{7,14}$'),
          state text NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'blocked')),
          created_at timestamptz NOT NULL DEFAULT now()
        );
        -- statement
        CREATE TABLE tenants (
          id uuid PRIMARY KEY,
          slug text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9]+(?:-[a-z0-9]+)*$'),
          display_name text NOT NULL CHECK (length(display_name) BETWEEN 1 AND 100),
          timezone text NOT NULL,
          status text NOT NULL DEFAULT 'active'
            CHECK (status IN ('trial', 'active', 'past_due', 'suspended', 'closed')),
          created_at timestamptz NOT NULL DEFAULT now()
        );
        -- statement
        CREATE TABLE tenant_memberships (
          tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
          user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          role text NOT NULL CHECK (role IN ('owner', 'admin', 'operator', 'courier')),
          state text NOT NULL DEFAULT 'active' CHECK (state IN ('invited', 'active', 'disabled')),
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (tenant_id, user_id)
        );
        -- statement
        CREATE INDEX ix_tenant_memberships_user ON tenant_memberships (user_id, state);
        -- statement
        CREATE TABLE auth_sessions (
          id uuid PRIMARY KEY,
          user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          token_hash bytea NOT NULL UNIQUE CHECK (octet_length(token_hash) = 32),
          expires_at timestamptz NOT NULL,
          revoked_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now(),
          CHECK (expires_at > created_at)
        );
        -- statement
        CREATE INDEX ix_auth_sessions_user ON auth_sessions (user_id, expires_at);

        -- statement
        ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
        -- statement
        ALTER TABLE tenants FORCE ROW LEVEL SECURITY;
        -- statement
        CREATE POLICY tenant_scope ON tenants
          USING (id = nullif(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        -- statement
        ALTER TABLE tenant_memberships ENABLE ROW LEVEL SECURITY;
        -- statement
        ALTER TABLE tenant_memberships FORCE ROW LEVEL SECURITY;
        -- statement
        CREATE POLICY tenant_scope ON tenant_memberships
          USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);

        -- statement
        CREATE FUNCTION list_session_tenants(p_token_hash bytea)
        RETURNS TABLE (tenant_id uuid, slug text, display_name text, timezone text, role text)
        LANGUAGE sql SECURITY DEFINER STABLE
        SET search_path = pg_catalog
        AS $function$
          SELECT t.id, t.slug, t.display_name, t.timezone, m.role
          FROM public.auth_sessions s
          JOIN public.users u ON u.id = s.user_id AND u.state = 'active'
          JOIN public.tenant_memberships m ON m.user_id = u.id AND m.state = 'active'
          JOIN public.tenants t ON t.id = m.tenant_id
            AND t.status IN ('trial', 'active', 'past_due')
          WHERE s.token_hash = p_token_hash
            AND s.revoked_at IS NULL AND s.expires_at > statement_timestamp()
          ORDER BY t.slug
        $function$;
        -- statement
        CREATE FUNCTION resolve_session_user(p_token_hash bytea)
        RETURNS uuid
        LANGUAGE sql SECURITY DEFINER STABLE
        SET search_path = pg_catalog
        AS $function$
          SELECT u.id
          FROM public.auth_sessions s
          JOIN public.users u ON u.id = s.user_id AND u.state = 'active'
          WHERE s.token_hash = p_token_hash
            AND s.revoked_at IS NULL AND s.expires_at > statement_timestamp()
        $function$;
        -- statement
        CREATE FUNCTION authorize_session_tenant(p_token_hash bytea, p_tenant_id uuid)
        RETURNS TABLE (user_id uuid, role text)
        LANGUAGE sql SECURITY DEFINER STABLE
        SET search_path = pg_catalog
        AS $function$
          SELECT u.id, m.role
          FROM public.auth_sessions s
          JOIN public.users u ON u.id = s.user_id AND u.state = 'active'
          JOIN public.tenant_memberships m ON m.user_id = u.id
            AND m.tenant_id = p_tenant_id AND m.state = 'active'
          JOIN public.tenants t ON t.id = m.tenant_id
            AND t.status IN ('trial', 'active', 'past_due')
          WHERE s.token_hash = p_token_hash
            AND s.revoked_at IS NULL AND s.expires_at > statement_timestamp()
        $function$;

        -- statement
        REVOKE ALL ON users, auth_sessions, tenants, tenant_memberships FROM PUBLIC;
        -- statement
        REVOKE ALL ON FUNCTION list_session_tenants(bytea) FROM PUBLIC;
        -- statement
        REVOKE ALL ON FUNCTION resolve_session_user(bytea) FROM PUBLIC;
        -- statement
        REVOKE ALL ON FUNCTION authorize_session_tenant(bytea, uuid) FROM PUBLIC;
        -- statement
        GRANT USAGE ON SCHEMA public TO photolamp_runtime;
        -- statement
        GRANT SELECT, INSERT, UPDATE, DELETE ON tenants, tenant_memberships TO photolamp_runtime;
        -- statement
        GRANT SELECT ON alembic_version TO photolamp_runtime;
        -- statement
        GRANT EXECUTE ON FUNCTION list_session_tenants(bytea) TO photolamp_runtime;
        -- statement
        GRANT EXECUTE ON FUNCTION resolve_session_user(bytea) TO photolamp_runtime;
        -- statement
        GRANT EXECUTE ON FUNCTION authorize_session_tenant(bytea, uuid) TO photolamp_runtime;
        """
    )


def downgrade() -> None:
    execute_script(
        """
        DROP FUNCTION IF EXISTS authorize_session_tenant(bytea, uuid);
        -- statement
        DROP FUNCTION IF EXISTS resolve_session_user(bytea);
        -- statement
        DROP FUNCTION IF EXISTS list_session_tenants(bytea);
        -- statement
        DROP TABLE IF EXISTS auth_sessions;
        -- statement
        DROP TABLE IF EXISTS tenant_memberships;
        -- statement
        DROP TABLE IF EXISTS tenants;
        -- statement
        DROP TABLE IF EXISTS users;
        """
    )

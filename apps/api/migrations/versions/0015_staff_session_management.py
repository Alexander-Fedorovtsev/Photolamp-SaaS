"""Privacy-minimized staff session inventory and revocation.

Revision ID: 0015_staff_session_management
Revises: 0014_staff_invitations_auth
"""

from alembic import op

revision = "0015_staff_session_management"
down_revision = "0014_staff_invitations_auth"
branch_labels = None
depends_on = None


def execute_script(script: str) -> None:
    for statement in script.split("\n        -- statement\n"):
        op.execute(statement)


def upgrade() -> None:
    execute_script(
        """
        ALTER TABLE auth_sessions
          ADD COLUMN client_label text NOT NULL DEFAULT 'Unknown client'
            CHECK (length(client_label) BETWEEN 1 AND 80),
          ADD COLUMN session_kind text NOT NULL DEFAULT 'staff'
            CHECK (session_kind IN ('staff', 'demo')),
          ADD COLUMN last_seen_at timestamptz NOT NULL DEFAULT now();
        -- statement
        UPDATE auth_sessions
        SET session_kind = 'demo', client_label = 'Existing demo session'
        WHERE user_id = 'd5000000-0000-0000-0000-000000000001';
        -- statement
        CREATE INDEX ix_auth_sessions_user_active
          ON auth_sessions (user_id, session_kind, expires_at DESC)
          WHERE revoked_at IS NULL;

        -- statement
        CREATE FUNCTION set_auth_session_metadata(
          p_token_hash bytea, p_client_label text, p_session_kind text
        ) RETURNS boolean
        LANGUAGE sql SECURITY DEFINER VOLATILE
        SET search_path = pg_catalog
        AS $function$
          UPDATE public.auth_sessions
          SET client_label = left(btrim(p_client_label), 80),
              session_kind = p_session_kind,
              last_seen_at = statement_timestamp()
          WHERE token_hash = p_token_hash
            AND octet_length(p_token_hash) = 32
            AND length(btrim(p_client_label)) BETWEEN 1 AND 80
            AND p_session_kind IN ('staff', 'demo')
            AND revoked_at IS NULL AND expires_at > statement_timestamp()
          RETURNING true
        $function$;

        -- statement
        CREATE FUNCTION touch_auth_session(p_token_hash bytea)
        RETURNS boolean
        LANGUAGE sql SECURITY DEFINER VOLATILE
        SET search_path = pg_catalog
        AS $function$
          UPDATE public.auth_sessions
          SET last_seen_at = statement_timestamp()
          WHERE token_hash = p_token_hash
            AND revoked_at IS NULL AND expires_at > statement_timestamp()
            AND last_seen_at < statement_timestamp() - interval '5 minutes'
          RETURNING true
        $function$;

        -- statement
        CREATE FUNCTION list_staff_sessions(p_current_token_hash bytea)
        RETURNS TABLE (
          session_id uuid, client_label text, created_at timestamptz,
          last_seen_at timestamptz, expires_at timestamptz, is_current boolean
        )
        LANGUAGE plpgsql SECURITY DEFINER VOLATILE
        SET search_path = pg_catalog
        AS $function$
        DECLARE
          current_session public.auth_sessions%ROWTYPE;
        BEGIN
          SELECT s.* INTO current_session
          FROM public.auth_sessions s
          JOIN public.users u ON u.id = s.user_id AND u.state = 'active'
          WHERE s.token_hash = p_current_token_hash
            AND s.revoked_at IS NULL AND s.expires_at > statement_timestamp();
          IF NOT FOUND THEN
            RETURN;
          END IF;
          UPDATE public.auth_sessions AS current_auth_session
          SET last_seen_at = statement_timestamp()
          WHERE current_auth_session.id = current_session.id
            AND current_auth_session.last_seen_at
              < statement_timestamp() - interval '5 minutes';
          RETURN QUERY
          SELECT s.id, s.client_label, s.created_at, s.last_seen_at, s.expires_at,
                 s.id = current_session.id
          FROM public.auth_sessions s
          WHERE s.user_id = current_session.user_id
            AND s.session_kind = current_session.session_kind
            AND s.revoked_at IS NULL AND s.expires_at > statement_timestamp()
            AND (current_session.session_kind = 'staff' OR s.id = current_session.id)
          ORDER BY (s.id = current_session.id) DESC, s.last_seen_at DESC, s.created_at DESC;
        END
        $function$;

        -- statement
        CREATE FUNCTION revoke_staff_session(p_current_token_hash bytea, p_session_id uuid)
        RETURNS smallint
        LANGUAGE plpgsql SECURITY DEFINER VOLATILE
        SET search_path = pg_catalog
        AS $function$
        DECLARE
          current_session public.auth_sessions%ROWTYPE;
          target_session public.auth_sessions%ROWTYPE;
          revoked_current boolean;
        BEGIN
          SELECT s.* INTO current_session
          FROM public.auth_sessions s
          JOIN public.users u ON u.id = s.user_id AND u.state = 'active'
          WHERE s.token_hash = p_current_token_hash
            AND s.revoked_at IS NULL AND s.expires_at > statement_timestamp()
          FOR UPDATE OF s;
          IF NOT FOUND THEN
            RETURN -1;
          END IF;
          SELECT s.* INTO target_session
          FROM public.auth_sessions s
          WHERE s.id = p_session_id
            AND s.user_id = current_session.user_id
            AND s.session_kind = current_session.session_kind
            AND s.revoked_at IS NULL AND s.expires_at > statement_timestamp()
            AND (current_session.session_kind = 'staff' OR s.id = current_session.id)
          FOR UPDATE;
          IF NOT FOUND THEN
            RETURN -2;
          END IF;
          revoked_current := target_session.id = current_session.id;
          UPDATE public.auth_sessions
          SET revoked_at = statement_timestamp()
          WHERE id = target_session.id;
          INSERT INTO public.audit_events
            (id, tenant_id, actor_user_id, action, entity_type, entity_id, changes)
          SELECT gen_random_uuid(), m.tenant_id, current_session.user_id,
                 'staff.session.revoked', 'auth_session', target_session.id,
                 jsonb_build_object('revoked_current', revoked_current)
          FROM public.tenant_memberships m
          WHERE m.user_id = current_session.user_id AND m.state = 'active';
          RETURN CASE WHEN revoked_current THEN 1 ELSE 0 END;
        END
        $function$;

        -- statement
        CREATE FUNCTION revoke_all_staff_sessions(p_current_token_hash bytea)
        RETURNS integer
        LANGUAGE plpgsql SECURITY DEFINER VOLATILE
        SET search_path = pg_catalog
        AS $function$
        DECLARE
          current_session public.auth_sessions%ROWTYPE;
          revoked_count integer;
        BEGIN
          SELECT s.* INTO current_session
          FROM public.auth_sessions s
          JOIN public.users u ON u.id = s.user_id AND u.state = 'active'
          WHERE s.token_hash = p_current_token_hash
            AND s.revoked_at IS NULL AND s.expires_at > statement_timestamp()
          FOR UPDATE OF s;
          IF NOT FOUND THEN
            RETURN NULL;
          END IF;
          UPDATE public.auth_sessions s
          SET revoked_at = statement_timestamp()
          WHERE s.user_id = current_session.user_id
            AND s.session_kind = current_session.session_kind
            AND s.revoked_at IS NULL AND s.expires_at > statement_timestamp()
            AND (current_session.session_kind = 'staff' OR s.id = current_session.id);
          GET DIAGNOSTICS revoked_count = ROW_COUNT;
          INSERT INTO public.audit_events
            (id, tenant_id, actor_user_id, action, entity_type, entity_id, changes)
          SELECT gen_random_uuid(), m.tenant_id, current_session.user_id,
                 'staff.sessions.revoked_all', 'user', current_session.user_id,
                 jsonb_build_object('revoked_count', revoked_count)
          FROM public.tenant_memberships m
          WHERE m.user_id = current_session.user_id AND m.state = 'active';
          RETURN revoked_count;
        END
        $function$;

        -- statement
        REVOKE ALL ON FUNCTION set_auth_session_metadata(bytea, text, text) FROM PUBLIC;
        -- statement
        REVOKE ALL ON FUNCTION touch_auth_session(bytea) FROM PUBLIC;
        -- statement
        REVOKE ALL ON FUNCTION list_staff_sessions(bytea) FROM PUBLIC;
        -- statement
        REVOKE ALL ON FUNCTION revoke_staff_session(bytea, uuid) FROM PUBLIC;
        -- statement
        REVOKE ALL ON FUNCTION revoke_all_staff_sessions(bytea) FROM PUBLIC;
        -- statement
        GRANT EXECUTE ON FUNCTION set_auth_session_metadata(bytea, text, text)
          TO photolamp_runtime;
        -- statement
        GRANT EXECUTE ON FUNCTION touch_auth_session(bytea) TO photolamp_runtime;
        -- statement
        GRANT EXECUTE ON FUNCTION list_staff_sessions(bytea) TO photolamp_runtime;
        -- statement
        GRANT EXECUTE ON FUNCTION revoke_staff_session(bytea, uuid) TO photolamp_runtime;
        -- statement
        GRANT EXECUTE ON FUNCTION revoke_all_staff_sessions(bytea) TO photolamp_runtime;
        """
    )


def downgrade() -> None:
    execute_script(
        """
        DROP FUNCTION IF EXISTS revoke_all_staff_sessions(bytea);
        -- statement
        DROP FUNCTION IF EXISTS revoke_staff_session(bytea, uuid);
        -- statement
        DROP FUNCTION IF EXISTS list_staff_sessions(bytea);
        -- statement
        DROP FUNCTION IF EXISTS touch_auth_session(bytea);
        -- statement
        DROP FUNCTION IF EXISTS set_auth_session_metadata(bytea, text, text);
        -- statement
        DROP INDEX IF EXISTS ix_auth_sessions_user_active;
        -- statement
        ALTER TABLE auth_sessions
          DROP COLUMN IF EXISTS last_seen_at,
          DROP COLUMN IF EXISTS session_kind,
          DROP COLUMN IF EXISTS client_label;
        """
    )

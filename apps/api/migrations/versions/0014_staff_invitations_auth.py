"""One-time staff invitations and password-backed staff authentication.

Revision ID: 0014_staff_invitations_auth
Revises: 0013_tenant_staff_management
"""

from alembic import op

revision = "0014_staff_invitations_auth"
down_revision = "0013_tenant_staff_management"
branch_labels = None
depends_on = None


def execute_script(script: str) -> None:
    for statement in script.split("\n        -- statement\n"):
        op.execute(statement)


def upgrade() -> None:
    execute_script(
        """
        ALTER TABLE users ALTER COLUMN phone_e164 DROP NOT NULL;
        -- statement
        CREATE TABLE staff_credentials (
          user_id uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
          login_name text NOT NULL
            CHECK (login_name ~ '^[a-z][a-z0-9._-]{3,31}$'),
          password_hash text NOT NULL CHECK (length(password_hash) BETWEEN 40 AND 255),
          failed_attempts integer NOT NULL DEFAULT 0 CHECK (failed_attempts >= 0),
          locked_until timestamptz,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now()
        );
        -- statement
        CREATE UNIQUE INDEX ux_staff_credentials_login_lower
          ON staff_credentials (lower(login_name));

        -- statement
        CREATE TABLE staff_invitations (
          id uuid PRIMARY KEY,
          tenant_id uuid NOT NULL,
          user_id uuid NOT NULL,
          created_by_user_id uuid NOT NULL REFERENCES users(id),
          token_hash bytea NOT NULL UNIQUE CHECK (octet_length(token_hash) = 32),
          state text NOT NULL DEFAULT 'pending'
            CHECK (state IN ('pending', 'accepted', 'revoked')),
          expires_at timestamptz NOT NULL,
          accepted_at timestamptz,
          revoked_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          FOREIGN KEY (tenant_id, user_id)
            REFERENCES tenant_memberships(tenant_id, user_id) ON DELETE CASCADE,
          CHECK (expires_at > created_at),
          CHECK ((state = 'accepted') = (accepted_at IS NOT NULL)),
          CHECK ((state = 'revoked') = (revoked_at IS NOT NULL)),
          UNIQUE (tenant_id, id),
          UNIQUE (tenant_id, user_id)
        );
        -- statement
        CREATE INDEX ix_staff_invitations_tenant_state_expires
          ON staff_invitations (tenant_id, state, expires_at);
        -- statement
        ALTER TABLE staff_invitations ENABLE ROW LEVEL SECURITY;
        -- statement
        ALTER TABLE staff_invitations FORCE ROW LEVEL SECURITY;
        -- statement
        CREATE POLICY tenant_scope ON staff_invitations
          USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);

        -- statement
        CREATE FUNCTION provision_staff_invitation(
          p_tenant_id uuid, p_actor_user_id uuid, p_invitation_id uuid, p_user_id uuid,
          p_token_hash bytea, p_expires_at timestamptz, p_role text, p_display_name text
        ) RETURNS void
        LANGUAGE plpgsql SECURITY DEFINER VOLATILE
        SET search_path = pg_catalog
        AS $function$
        BEGIN
          IF nullif(current_setting('app.tenant_id', true), '')::uuid IS DISTINCT FROM p_tenant_id
             OR octet_length(p_token_hash) <> 32
             OR p_expires_at <= statement_timestamp()
             OR p_expires_at > statement_timestamp() + interval '7 days'
             OR p_role NOT IN ('owner', 'admin', 'operator', 'courier')
             OR length(btrim(p_display_name)) NOT BETWEEN 1 AND 120
             OR NOT EXISTS (
               SELECT 1 FROM public.tenant_memberships
               WHERE tenant_id = p_tenant_id AND user_id = p_actor_user_id
                 AND role = 'owner' AND state = 'active'
             ) THEN
            RAISE EXCEPTION 'invalid staff invitation request';
          END IF;
          INSERT INTO public.users (id, phone_e164) VALUES (p_user_id, NULL);
          INSERT INTO public.tenant_memberships
            (tenant_id, user_id, role, state, display_name)
          VALUES (p_tenant_id, p_user_id, p_role, 'invited', btrim(p_display_name));
          INSERT INTO public.staff_invitations
            (id, tenant_id, user_id, created_by_user_id, token_hash, expires_at)
          VALUES (
            p_invitation_id, p_tenant_id, p_user_id, p_actor_user_id,
            p_token_hash, p_expires_at
          );
        END
        $function$;

        -- statement
        CREATE FUNCTION redeem_staff_invitation(
          p_token_hash bytea, p_login_name text, p_password_hash text,
          p_session_id uuid, p_session_token_hash bytea, p_session_expires_at timestamptz
        ) RETURNS TABLE (user_id uuid, tenant_id uuid, expires_at timestamptz)
        LANGUAGE plpgsql SECURITY DEFINER VOLATILE
        SET search_path = pg_catalog
        AS $function$
        DECLARE
          invitation public.staff_invitations%ROWTYPE;
          normalized_login text := lower(btrim(p_login_name));
        BEGIN
          IF octet_length(p_token_hash) <> 32
             OR octet_length(p_session_token_hash) <> 32
             OR normalized_login !~ '^[a-z][a-z0-9._-]{3,31}$'
             OR p_password_hash NOT LIKE '$scrypt$%'
             OR p_session_expires_at <= statement_timestamp()
             OR p_session_expires_at > statement_timestamp() + interval '7 days' THEN
            RETURN;
          END IF;
          SELECT i.* INTO invitation
          FROM public.staff_invitations i
          WHERE i.token_hash = p_token_hash
          FOR UPDATE;
          IF NOT FOUND OR invitation.state <> 'pending'
             OR invitation.expires_at <= statement_timestamp() THEN
            RETURN;
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM public.tenant_memberships m
            JOIN public.users u ON u.id = m.user_id AND u.state = 'active'
            WHERE m.tenant_id = invitation.tenant_id
              AND m.user_id = invitation.user_id AND m.state = 'invited'
          ) THEN
            RETURN;
          END IF;
          INSERT INTO public.staff_credentials (user_id, login_name, password_hash)
          VALUES (invitation.user_id, normalized_login, p_password_hash);
          UPDATE public.tenant_memberships
          SET state = 'active', updated_at = statement_timestamp()
          WHERE tenant_memberships.tenant_id = invitation.tenant_id
            AND tenant_memberships.user_id = invitation.user_id;
          UPDATE public.staff_invitations
          SET state = 'accepted', accepted_at = statement_timestamp(),
              updated_at = statement_timestamp()
          WHERE id = invitation.id;
          INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at)
          VALUES (
            p_session_id, invitation.user_id, p_session_token_hash,
            p_session_expires_at
          );
          INSERT INTO public.audit_events
            (id, tenant_id, actor_user_id, action, entity_type, entity_id, changes)
          VALUES (
            gen_random_uuid(), invitation.tenant_id, invitation.user_id,
            'staff.invitation.accepted', 'tenant_membership', invitation.user_id,
            jsonb_build_object(
              'before', jsonb_build_object('state', 'invited'),
              'after', jsonb_build_object('state', 'active')
            )
          );
          RETURN QUERY SELECT invitation.user_id, invitation.tenant_id, p_session_expires_at;
        EXCEPTION
          WHEN unique_violation THEN
            RETURN;
        END
        $function$;

        -- statement
        CREATE FUNCTION lookup_staff_login(p_login_name text)
        RETURNS TABLE (
          user_id uuid, password_hash text, failed_attempts integer, locked_until timestamptz
        )
        LANGUAGE sql SECURITY DEFINER STABLE
        SET search_path = pg_catalog
        AS $function$
          SELECT c.user_id, c.password_hash, c.failed_attempts, c.locked_until
          FROM public.staff_credentials c
          JOIN public.users u ON u.id = c.user_id AND u.state = 'active'
          WHERE lower(c.login_name) = lower(btrim(p_login_name))
            AND EXISTS (
              SELECT 1 FROM public.tenant_memberships m
              WHERE m.user_id = c.user_id AND m.state = 'active'
            )
        $function$;

        -- statement
        CREATE FUNCTION register_staff_login_failure(p_login_name text)
        RETURNS timestamptz
        LANGUAGE sql SECURITY DEFINER VOLATILE
        SET search_path = pg_catalog
        AS $function$
          UPDATE public.staff_credentials
          SET failed_attempts = failed_attempts + 1,
              locked_until = CASE
                WHEN failed_attempts + 1 >= 5
                THEN statement_timestamp() + interval '15 minutes'
                ELSE locked_until
              END,
              updated_at = statement_timestamp()
          WHERE lower(login_name) = lower(btrim(p_login_name))
          RETURNING locked_until
        $function$;

        -- statement
        CREATE FUNCTION issue_staff_login_session(
          p_login_name text, p_session_id uuid, p_token_hash bytea, p_expires_at timestamptz
        ) RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER VOLATILE
        SET search_path = pg_catalog
        AS $function$
        DECLARE
          resolved_user_id uuid;
        BEGIN
          IF octet_length(p_token_hash) <> 32
             OR p_expires_at <= statement_timestamp()
             OR p_expires_at > statement_timestamp() + interval '7 days' THEN
            RETURN NULL;
          END IF;
          UPDATE public.staff_credentials c
          SET failed_attempts = 0, locked_until = NULL, updated_at = statement_timestamp()
          FROM public.users u
          WHERE c.user_id = u.id AND u.state = 'active'
            AND lower(c.login_name) = lower(btrim(p_login_name))
            AND (c.locked_until IS NULL OR c.locked_until <= statement_timestamp())
            AND EXISTS (
              SELECT 1 FROM public.tenant_memberships m
              WHERE m.user_id = c.user_id AND m.state = 'active'
            )
          RETURNING c.user_id INTO resolved_user_id;
          IF resolved_user_id IS NULL THEN
            RETURN NULL;
          END IF;
          INSERT INTO public.auth_sessions (id, user_id, token_hash, expires_at)
          VALUES (p_session_id, resolved_user_id, p_token_hash, p_expires_at);
          RETURN resolved_user_id;
        END
        $function$;

        -- statement
        REVOKE ALL ON staff_credentials, staff_invitations FROM PUBLIC;
        -- statement
        REVOKE ALL ON FUNCTION provision_staff_invitation(
          uuid, uuid, uuid, uuid, bytea, timestamptz, text, text
        ) FROM PUBLIC;
        -- statement
        REVOKE ALL ON FUNCTION redeem_staff_invitation(
          bytea, text, text, uuid, bytea, timestamptz
        ) FROM PUBLIC;
        -- statement
        REVOKE ALL ON FUNCTION lookup_staff_login(text) FROM PUBLIC;
        -- statement
        REVOKE ALL ON FUNCTION register_staff_login_failure(text) FROM PUBLIC;
        -- statement
        REVOKE ALL ON FUNCTION issue_staff_login_session(text, uuid, bytea, timestamptz)
          FROM PUBLIC;
        -- statement
        GRANT SELECT, UPDATE ON staff_invitations TO photolamp_runtime;
        -- statement
        GRANT EXECUTE ON FUNCTION provision_staff_invitation(
          uuid, uuid, uuid, uuid, bytea, timestamptz, text, text
        ) TO photolamp_runtime;
        -- statement
        GRANT EXECUTE ON FUNCTION redeem_staff_invitation(
          bytea, text, text, uuid, bytea, timestamptz
        ) TO photolamp_runtime;
        -- statement
        GRANT EXECUTE ON FUNCTION lookup_staff_login(text) TO photolamp_runtime;
        -- statement
        GRANT EXECUTE ON FUNCTION register_staff_login_failure(text) TO photolamp_runtime;
        -- statement
        GRANT EXECUTE ON FUNCTION issue_staff_login_session(text, uuid, bytea, timestamptz)
          TO photolamp_runtime;
        """
    )


def downgrade() -> None:
    execute_script(
        """
        DO $block$
        BEGIN
          IF EXISTS (SELECT 1 FROM users WHERE phone_e164 IS NULL) THEN
            RAISE EXCEPTION
              'cannot downgrade staff auth while password identities exist';
          END IF;
        END
        $block$;
        -- statement
        DROP FUNCTION IF EXISTS issue_staff_login_session(text, uuid, bytea, timestamptz);
        -- statement
        DROP FUNCTION IF EXISTS register_staff_login_failure(text);
        -- statement
        DROP FUNCTION IF EXISTS lookup_staff_login(text);
        -- statement
        DROP FUNCTION IF EXISTS redeem_staff_invitation(
          bytea, text, text, uuid, bytea, timestamptz
        );
        -- statement
        DROP FUNCTION IF EXISTS provision_staff_invitation(
          uuid, uuid, uuid, uuid, bytea, timestamptz, text, text
        );
        -- statement
        DROP TABLE IF EXISTS staff_invitations;
        -- statement
        DROP TABLE IF EXISTS staff_credentials;
        -- statement
        ALTER TABLE users ALTER COLUMN phone_e164 SET NOT NULL;
        """
    )

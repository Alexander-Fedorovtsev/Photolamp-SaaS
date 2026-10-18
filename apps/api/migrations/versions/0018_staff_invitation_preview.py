"""Minimal capability-authorized preview of a still redeemable invitation."""

from alembic import op

revision = "0018_staff_invitation_preview"
down_revision = "0017_courier_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION preview_staff_invitation(p_token_hash bytea)
        RETURNS TABLE (
          organization_name text, display_label text, role text, expires_at timestamptz
        )
        LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog AS $function$
          SELECT t.display_name, coalesce(m.display_name, 'Сотрудник'), m.role, i.expires_at
          FROM public.staff_invitations i
          JOIN public.tenant_memberships m ON m.tenant_id = i.tenant_id AND m.user_id = i.user_id
          JOIN public.users u ON u.id = m.user_id AND u.state = 'active'
          JOIN public.tenants t ON t.id = i.tenant_id
            AND t.status IN ('active', 'trial', 'past_due')
          WHERE i.token_hash = p_token_hash AND i.state = 'pending'
            AND i.expires_at > statement_timestamp() AND m.state = 'invited'
            AND NOT EXISTS (SELECT 1 FROM public.staff_credentials c WHERE c.user_id = i.user_id)
        $function$;
    """)
    op.execute("REVOKE ALL ON FUNCTION preview_staff_invitation(bytea) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION preview_staff_invitation(bytea) TO photolamp_runtime")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS preview_staff_invitation(bytea)")

"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import {
  staffInvitationCreatedSchema,
  staffInvitationSummarySchema,
  staffMemberSchema,
  teamSnapshotSchema,
  type StaffInvitationSummary,
  type StaffMember,
  type TeamSnapshot,
} from "@/lib/admin";

const roleLabels = {
  owner: "Владелец",
  admin: "Администратор",
  operator: "Оператор",
  courier: "Курьер",
} as const;

const stateLabels = {
  active: "Доступ активен",
  invited: "Ожидает подключения",
  disabled: "Доступ отключён",
} as const;

async function readPayload(response: Response) {
  return (await response.json().catch(() => ({}))) as unknown;
}

function formatExpiry(value: string) {
  return new Intl.DateTimeFormat("ru-RU", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export function AdminTeam({
  tenantId,
  onUnauthorized,
}: {
  tenantId: string;
  onUnauthorized: () => void;
}) {
  const [team, setTeam] = useState<TeamSnapshot | null>(null);
  const [invitations, setInvitations] = useState<StaffInvitationSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyUserId, setBusyUserId] = useState("");
  const [showInviteForm, setShowInviteForm] = useState(false);
  const [invitationLink, setInvitationLink] = useState("");
  const [notice, setNotice] = useState("");

  const loadTeam = useCallback(async () => {
    setLoading(true);
    setNotice("");
    try {
      const response = await fetch(`/api/admin/team?tenant_id=${encodeURIComponent(tenantId)}`, {
        cache: "no-store",
      });
      if (response.status === 401) {
        onUnauthorized();
        return;
      }
      const payload = await readPayload(response);
      if (!response.ok) {
        throw new Error((payload as { detail?: string }).detail ?? "Не удалось загрузить команду");
      }
      const nextTeam = teamSnapshotSchema.parse(payload);
      setTeam(nextTeam);
      if (nextTeam.can_manage) {
        const inviteResponse = await fetch(
          `/api/admin/team/invitations?tenant_id=${encodeURIComponent(tenantId)}`,
          { cache: "no-store" },
        );
        const invitePayload = await readPayload(inviteResponse);
        if (!inviteResponse.ok) {
          throw new Error(
            (invitePayload as { detail?: string }).detail ?? "Не удалось загрузить приглашения",
          );
        }
        setInvitations(staffInvitationSummarySchema.array().parse(invitePayload));
      } else {
        setInvitations([]);
      }
    } catch (error) {
      setTeam(null);
      setInvitations([]);
      setNotice(error instanceof Error ? error.message : "Не удалось загрузить команду");
    } finally {
      setLoading(false);
    }
  }, [onUnauthorized, tenantId]);

  useEffect(() => {
    // Tenant selection is the external input for the protected team read model.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadTeam();
  }, [loadTeam]);

  async function createInvitation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    setBusyUserId("invitation");
    setNotice("");
    setInvitationLink("");
    try {
      const response = await fetch(
        `/api/admin/team/invitations?tenant_id=${encodeURIComponent(tenantId)}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            display_name: data.get("display_name"),
            role: data.get("role"),
          }),
        },
      );
      if (response.status === 401) {
        onUnauthorized();
        return;
      }
      const payload = await readPayload(response);
      if (!response.ok) {
        throw new Error(
          (payload as { detail?: string }).detail ?? "Не удалось создать приглашение",
        );
      }
      const created = staffInvitationCreatedSchema.parse(payload);
      const fragment = new URLSearchParams({ token: created.invitation_token }).toString();
      setInvitationLink(`${window.location.origin}/admin/invite#${fragment}`);
      setTeam((current) =>
        current ? { ...current, members: [...current.members, created.member] } : current,
      );
      setInvitations((current) => [
        {
          invitation_id: created.invitation_id,
          user_id: created.member.user_id,
          state: "pending",
          expires_at: created.expires_at,
          created_at: created.member.created_at,
        },
        ...current,
      ]);
      form.reset();
      setShowInviteForm(false);
      setNotice("Приглашение создано. Передайте ссылку сотруднику безопасным способом.");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Не удалось создать приглашение");
    } finally {
      setBusyUserId("");
    }
  }

  async function copyInvitation() {
    try {
      await navigator.clipboard.writeText(invitationLink);
      setNotice("Одноразовая ссылка скопирована.");
    } catch {
      setNotice("Не удалось скопировать автоматически. Выделите ссылку вручную.");
    }
  }

  async function revokeInvitation(invitation: StaffInvitationSummary) {
    if (!window.confirm("Отозвать это приглашение? Ссылка перестанет работать.")) return;
    setBusyUserId(invitation.user_id);
    setNotice("");
    try {
      const response = await fetch(
        `/api/admin/team/invitations/${invitation.invitation_id}?tenant_id=${encodeURIComponent(tenantId)}`,
        { method: "DELETE" },
      );
      if (response.status === 401) {
        onUnauthorized();
        return;
      }
      const payload = await readPayload(response);
      if (!response.ok) {
        throw new Error(
          (payload as { detail?: string }).detail ?? "Не удалось отозвать приглашение",
        );
      }
      await loadTeam();
      setNotice("Приглашение отозвано, доступ не активирован.");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Не удалось отозвать приглашение");
    } finally {
      setBusyUserId("");
    }
  }

  async function updateMember(member: StaffMember, body: object) {
    setBusyUserId(member.user_id);
    setNotice("");
    try {
      const response = await fetch(
        `/api/admin/team/${encodeURIComponent(member.user_id)}?tenant_id=${encodeURIComponent(tenantId)}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
      );
      if (response.status === 401) {
        onUnauthorized();
        return;
      }
      const payload = await readPayload(response);
      if (!response.ok) {
        throw new Error((payload as { detail?: string }).detail ?? "Не удалось изменить доступ");
      }
      const updated = staffMemberSchema.parse(payload);
      setTeam((current) =>
        current
          ? {
              ...current,
              members: current.members.map((item) =>
                item.user_id === updated.user_id ? updated : item,
              ),
            }
          : current,
      );
      setNotice("Изменения доступа сохранены и записаны в аудит.");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Не удалось изменить доступ");
    } finally {
      setBusyUserId("");
    }
  }

  return (
    <section className="admin-section admin-team" aria-labelledby="team-title">
      <div className="admin-section-heading">
        <div>
          <p className="eyebrow">Доступ к организации</p>
          <h2 id="team-title">Команда и роли</h2>
          <p className="small">
            В списке нет телефонов и учётных данных. Изменения ролей аудируются.
          </p>
        </div>
        <button
          className="button secondary"
          type="button"
          disabled={!team?.can_manage}
          onClick={() => setShowInviteForm((current) => !current)}
        >
          Добавить сотрудника
        </button>
      </div>

      <p className="admin-team-boundary">
        Система создаёт одноразовую ссылку на 48 часов, но не отправляет её по SMS. Владелец
        передаёт ссылку сотруднику самостоятельно; после активации повторно использовать её нельзя.
      </p>

      {showInviteForm && team?.can_manage && (
        <form className="admin-invitation-form" onSubmit={(event) => void createInvitation(event)}>
          <label>
            Имя в команде
            <input name="display_name" required minLength={1} maxLength={120} />
          </label>
          <label>
            Роль
            <select name="role" defaultValue="operator">
              <option value="owner">Владелец</option>
              <option value="admin">Администратор</option>
              <option value="operator">Оператор</option>
              <option value="courier">Курьер</option>
            </select>
          </label>
          <div className="admin-form-actions">
            <button className="button primary" type="submit" disabled={busyUserId === "invitation"}>
              {busyUserId === "invitation" ? "Создаём…" : "Создать ссылку"}
            </button>
            <button
              className="button secondary"
              type="button"
              onClick={() => setShowInviteForm(false)}
            >
              Отмена
            </button>
          </div>
        </form>
      )}

      {invitationLink && (
        <div className="admin-invitation-link" role="status">
          <strong>Скопируйте ссылку сейчас</strong>
          <p>После обновления страницы она не будет показана повторно.</p>
          <div>
            <input value={invitationLink} readOnly aria-label="Одноразовая ссылка приглашения" />
            <button
              className="button secondary"
              type="button"
              onClick={() => void copyInvitation()}
            >
              Скопировать
            </button>
          </div>
        </div>
      )}

      {notice && (
        <p className="admin-alert" role="status" aria-live="polite">
          {notice}
        </p>
      )}
      {loading ? (
        <p className="admin-inline-loading">Загружаем команду…</p>
      ) : !team ? null : (
        <div className="admin-team-list">
          {team.members.map((member) => (
            <TeamMemberCard
              key={`${member.user_id}:${member.updated_at}`}
              member={member}
              invitation={invitations.find((item) => item.user_id === member.user_id)}
              canManage={team.can_manage}
              busy={busyUserId === member.user_id}
              onSave={updateMember}
              onRevoke={revokeInvitation}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function TeamMemberCard({
  member,
  invitation,
  canManage,
  busy,
  onSave,
  onRevoke,
}: {
  member: StaffMember;
  invitation?: StaffInvitationSummary;
  canManage: boolean;
  busy: boolean;
  onSave: (member: StaffMember, body: object) => Promise<void>;
  onRevoke: (invitation: StaffInvitationSummary) => Promise<void>;
}) {
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const body = {
      display_name: String(data.get("display_name")),
      role: String(data.get("role")),
      state: String(data.get("state")),
    };
    if (!window.confirm(`Сохранить права для «${member.display_name}»?`)) return;
    void onSave(member, body);
  }

  return (
    <article className={member.state === "disabled" ? "is-disabled" : ""}>
      <div className="admin-team-member-heading">
        <div>
          <span className={`status status-${member.state}`}>{stateLabels[member.state]}</span>
          <h3>{member.display_name}</h3>
          <p>
            {roleLabels[member.role]}
            {member.is_current_user ? " · это вы" : ""}
          </p>
        </div>
        <span className="admin-team-id">ID {member.user_id.slice(0, 8)}</span>
      </div>

      {canManage && !member.is_current_user && member.state !== "invited" ? (
        <form onSubmit={submit}>
          <label>
            Отображаемое имя
            <input
              name="display_name"
              defaultValue={member.display_name}
              required
              minLength={1}
              maxLength={120}
            />
          </label>
          <label>
            Роль
            <select name="role" defaultValue={member.role}>
              <option value="owner">Владелец</option>
              <option value="admin">Администратор</option>
              <option value="operator">Оператор</option>
              <option value="courier">Курьер</option>
            </select>
          </label>
          <label>
            Доступ
            <select name="state" defaultValue={member.state}>
              <option value="active">Активен</option>
              <option value="disabled">Отключён</option>
            </select>
          </label>
          <button className="button secondary" type="submit" disabled={busy}>
            {busy ? "Сохраняем…" : "Сохранить права"}
          </button>
        </form>
      ) : (
        <div className="admin-team-readonly">
          <p>
            {member.is_current_user
              ? "Собственную роль и доступ нельзя изменить из этой сессии."
              : member.state === "invited"
                ? invitation?.state === "expired"
                  ? "Срок одноразовой ссылки истёк. Отзовите её и создайте новую."
                  : invitation
                    ? `Ссылка действует до ${formatExpiry(invitation.expires_at)}.`
                    : "Приглашение ожидает активации."
                : "Изменять роли может только владелец организации."}
          </p>
          {canManage &&
            invitation &&
            invitation.state !== "accepted" &&
            invitation.state !== "revoked" && (
              <button
                className="button secondary"
                type="button"
                disabled={busy}
                onClick={() => void onRevoke(invitation)}
              >
                {busy ? "Отзываем…" : "Отозвать приглашение"}
              </button>
            )}
        </div>
      )}
    </article>
  );
}

"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import {
  staffMemberSchema,
  teamSnapshotSchema,
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

export function AdminTeam({
  tenantId,
  onUnauthorized,
}: {
  tenantId: string;
  onUnauthorized: () => void;
}) {
  const [team, setTeam] = useState<TeamSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyUserId, setBusyUserId] = useState("");
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
      setTeam(teamSnapshotSchema.parse(payload));
    } catch (error) {
      setTeam(null);
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
            В списке нет телефонов и других контактных данных. Изменения ролей аудируются.
          </p>
        </div>
        <button className="button secondary" type="button" disabled>
          Добавить сотрудника
        </button>
      </div>

      <p className="admin-team-boundary">
        Приглашения появятся после выбора production-аутентификации. Сейчас система не отправляет
        SMS и не создаёт неработающие приглашения.
      </p>

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
              canManage={team.can_manage}
              busy={busyUserId === member.user_id}
              onSave={updateMember}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function TeamMemberCard({
  member,
  canManage,
  busy,
  onSave,
}: {
  member: StaffMember;
  canManage: boolean;
  busy: boolean;
  onSave: (member: StaffMember, body: object) => Promise<void>;
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
        <p className="admin-team-readonly">
          {member.is_current_user
            ? "Собственную роль и доступ нельзя изменить из этой сессии."
            : member.state === "invited"
              ? "Активация приглашения недоступна до запуска production-аутентификации."
              : "Изменять роли может только владелец организации."}
        </p>
      )}
    </article>
  );
}

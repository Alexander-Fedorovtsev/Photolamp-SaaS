"use client";

import { useCallback, useEffect, useState } from "react";
import {
  staffSessionRevokedSchema,
  staffSessionSummarySchema,
  type StaffSessionSummary,
} from "@/lib/admin";

function formatTime(value: string) {
  return new Intl.DateTimeFormat("ru-RU", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

async function readPayload(response: Response) {
  return (await response.json().catch(() => ({}))) as unknown;
}

export function AdminSessions({ onUnauthorized }: { onUnauthorized: () => void }) {
  const [sessions, setSessions] = useState<StaffSessionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState("");
  const [notice, setNotice] = useState("");

  const loadSessions = useCallback(async () => {
    setLoading(true);
    try {
      const response = await fetch("/api/admin/sessions", { cache: "no-store" });
      if (response.status === 401) {
        onUnauthorized();
        return;
      }
      const payload = await readPayload(response);
      if (!response.ok) {
        throw new Error(
          (payload as { detail?: string }).detail ?? "Не удалось загрузить активные сессии",
        );
      }
      setSessions(staffSessionSummarySchema.array().parse(payload));
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Не удалось загрузить активные сессии");
    } finally {
      setLoading(false);
    }
  }, [onUnauthorized]);

  useEffect(() => {
    // Session inventory is bootstrapped from the authenticated server boundary.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadSessions();
  }, [loadSessions]);

  async function revokeSession(session: StaffSessionSummary) {
    const question = session.is_current
      ? "Завершить текущую сессию? Потребуется войти снова."
      : `Завершить сессию «${session.client_label}»?`;
    if (!window.confirm(question)) return;
    setBusyId(session.session_id);
    setNotice("");
    try {
      const response = await fetch(`/api/admin/sessions/${session.session_id}`, {
        method: "DELETE",
      });
      if (response.status === 401) {
        onUnauthorized();
        return;
      }
      const payload = await readPayload(response);
      if (!response.ok) {
        throw new Error((payload as { detail?: string }).detail ?? "Не удалось завершить сессию");
      }
      const revoked = staffSessionRevokedSchema.parse(payload);
      if (revoked.revoked_current) {
        onUnauthorized();
        return;
      }
      await loadSessions();
      setNotice("Сессия завершена.");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Не удалось завершить сессию");
    } finally {
      setBusyId("");
    }
  }

  async function revokeAllSessions() {
    if (
      !window.confirm("Завершить все активные сессии? На этом устройстве потребуется войти снова.")
    ) {
      return;
    }
    setBusyId("all");
    setNotice("");
    try {
      const response = await fetch("/api/admin/sessions", { method: "DELETE" });
      if (response.status === 401) {
        onUnauthorized();
        return;
      }
      if (!response.ok) {
        const payload = (await readPayload(response)) as { detail?: string };
        throw new Error(payload.detail ?? "Не удалось завершить все сессии");
      }
      onUnauthorized();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Не удалось завершить все сессии");
      setBusyId("");
    }
  }

  return (
    <section className="admin-section admin-sessions" aria-labelledby="sessions-title">
      <div className="admin-section-heading">
        <div>
          <p className="eyebrow">Безопасность входа</p>
          <h2 id="sessions-title">Активные сессии</h2>
          <p className="small">
            Photolamp хранит только грубую подпись браузера и время активности — без IP и полного
            User-Agent.
          </p>
        </div>
        <button
          className="button secondary"
          type="button"
          disabled={loading || busyId !== ""}
          onClick={() => void revokeAllSessions()}
        >
          {busyId === "all" ? "Завершаем…" : "Выйти на всех устройствах"}
        </button>
      </div>

      {notice && (
        <p className="admin-alert" role="status" aria-live="polite">
          {notice}
        </p>
      )}
      {loading ? (
        <p className="admin-loading">Загружаем сессии…</p>
      ) : (
        <div className="admin-session-list">
          {sessions.map((session) => (
            <article className="admin-session-card" key={session.session_id}>
              <div>
                <div className="admin-session-title">
                  <strong>{session.client_label}</strong>
                  {session.is_current && <span className="status status-active">Текущая</span>}
                </div>
                <p>
                  Последняя активность: {formatTime(session.last_seen_at)} · действует до{" "}
                  {formatTime(session.expires_at)}
                </p>
              </div>
              <button
                className="button secondary"
                type="button"
                disabled={busyId !== ""}
                onClick={() => void revokeSession(session)}
              >
                {busyId === session.session_id ? "Завершаем…" : "Завершить"}
              </button>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

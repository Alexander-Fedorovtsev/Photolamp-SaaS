"use client";

import { useCallback, useEffect, useState } from "react";
import {
  courierContactResultSchema,
  courierTaskPageSchema,
  courierTaskResultSchema,
  type CourierContact,
  type CourierTask,
  type CourierTaskPage,
} from "@/lib/admin";

const stateLabels = {
  assigned: "Новое задание",
  accepted: "Принято",
  picked_up: "Лампа у вас",
  delivered: "Доставлено",
} as const;

function formatDate(value: string) {
  return new Intl.DateTimeFormat("ru-RU", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${value}T12:00:00Z`));
}

async function payload(response: Response) {
  return (await response.json().catch(() => ({}))) as unknown;
}

export function CourierWorkspace({
  tenantId,
  onUnauthorized,
  onAccessChanged,
}: {
  tenantId: string;
  onUnauthorized: () => void;
  onAccessChanged: () => void;
}) {
  const [page, setPage] = useState<CourierTaskPage | null>(null);
  const [contacts, setContacts] = useState<Record<string, CourierContact>>({});
  const [busyTaskId, setBusyTaskId] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);

  const loadTasks = useCallback(async () => {
    setLoading(true);
    setContacts({});
    try {
      const response = await fetch(
        `/api/admin/courier/tasks?tenant_id=${encodeURIComponent(tenantId)}`,
        { cache: "no-store" },
      );
      if (response.status === 401) {
        onUnauthorized();
        return;
      }
      const data = await payload(response);
      const detail = (data as { detail?: unknown }).detail;
      if (response.status === 403 || (response.status === 404 && detail === "Tenant not found")) {
        setPage(null);
        onAccessChanged();
        return;
      }
      if (!response.ok) {
        throw new Error((data as { detail?: string }).detail ?? "Не удалось загрузить задания");
      }
      setPage(courierTaskPageSchema.parse(data));
    } catch (error) {
      setPage(null);
      setNotice(error instanceof Error ? error.message : "Не удалось загрузить задания");
    } finally {
      setLoading(false);
    }
  }, [onAccessChanged, onUnauthorized, tenantId]);

  useEffect(() => {
    // The selected membership is the external source for the courier queue.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadTasks();
  }, [loadTasks]);

  async function transition(task: CourierTask, action: "accept" | "pickup" | "deliver") {
    const prompts = {
      accept: "принять задание",
      pickup: "подтвердить получение лампы в филиале",
      deliver: "подтвердить доставку клиенту",
    };
    if (!window.confirm(`Действительно ${prompts[action]}?`)) return;
    setBusyTaskId(task.task_id);
    setNotice("");
    try {
      const response = await fetch(
        `/api/admin/courier/tasks/${encodeURIComponent(task.task_id)}/${action}?tenant_id=${encodeURIComponent(tenantId)}`,
        {
          method: "POST",
          headers: { "Idempotency-Key": `courier-${action}-${crypto.randomUUID()}` },
        },
      );
      if (response.status === 401) {
        onUnauthorized();
        return;
      }
      const data = await payload(response);
      if (response.status === 403) {
        setPage(null);
        setContacts({});
        onAccessChanged();
        return;
      }
      if (!response.ok) {
        throw new Error((data as { detail?: string }).detail ?? "Не удалось обновить задание");
      }
      courierTaskResultSchema.parse(data);
      await loadTasks();
      setNotice(
        action === "accept"
          ? "Задание принято. Контакт доставки теперь доступен."
          : action === "pickup"
            ? "Получение отмечено. Лампа числится у клиента."
            : "Доставка отмечена как выполненная.",
      );
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Не удалось обновить задание");
    } finally {
      setBusyTaskId("");
    }
  }

  async function revealContact(task: CourierTask) {
    setBusyTaskId(task.task_id);
    setNotice("");
    try {
      const response = await fetch(
        `/api/admin/courier/tasks/${encodeURIComponent(task.task_id)}/contact?tenant_id=${encodeURIComponent(tenantId)}`,
        {
          method: "POST",
          headers: { "Idempotency-Key": `courier-contact-${crypto.randomUUID()}` },
        },
      );
      if (response.status === 401) {
        onUnauthorized();
        return;
      }
      const data = await payload(response);
      if (response.status === 403) {
        setPage(null);
        setContacts({});
        onAccessChanged();
        return;
      }
      if (!response.ok) {
        throw new Error((data as { detail?: string }).detail ?? "Контакт недоступен");
      }
      const contact = courierContactResultSchema.parse(data).contact;
      setContacts((current) => ({ ...current, [task.task_id]: contact }));
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Контакт недоступен");
    } finally {
      setBusyTaskId("");
    }
  }

  return (
    <section className="admin-section courier-workspace" aria-labelledby="courier-workspace-title">
      <div className="admin-section-heading">
        <div>
          <p className="eyebrow">Доставка</p>
          <h2 id="courier-workspace-title">Мои задания</h2>
          <p className="small">
            Только назначенные вам доставки. Контакт открывается после принятия задания и
            записывается в аудит.
          </p>
        </div>
        <button className="button secondary" type="button" onClick={() => void loadTasks()}>
          Обновить
        </button>
      </div>
      {notice && (
        <p className="admin-alert" role="status" aria-live="polite">
          {notice}
        </p>
      )}
      {loading ? (
        <p className="admin-inline-loading">Загружаем задания…</p>
      ) : !page ? null : page.items.length === 0 ? (
        <div className="admin-empty-state">
          <h3>Назначенных доставок нет</h3>
          <p>Новые задания появятся после назначения оператором.</p>
        </div>
      ) : (
        <div className="courier-task-grid">
          {page.items.map((task) => {
            const contact = contacts[task.task_id];
            const busy = busyTaskId === task.task_id;
            return (
              <article className="courier-task-card" key={task.task_id}>
                <div className="courier-task-heading">
                  <span className={`status status-${task.state}`}>{stateLabels[task.state]}</span>
                  <span className="small">#{task.order_id.slice(0, 8)}</span>
                </div>
                <h3>{task.snapshot.model_name}</h3>
                <dl>
                  <div>
                    <dt>Забрать</dt>
                    <dd>{task.snapshot.branch_name}</dd>
                  </div>
                  <div>
                    <dt>Период аренды</dt>
                    <dd>
                      {formatDate(task.snapshot.start_date)} — {formatDate(task.planned_end_date)}
                    </dd>
                  </div>
                </dl>
                {contact && (
                  <div className="admin-contact-panel courier-contact-panel">
                    <div>
                      <span>Получатель</span>
                      <strong>{contact.recipient_name}</strong>
                    </div>
                    <div>
                      <span>Телефон</span>
                      <a href={`tel:${contact.phone_e164}`}>{contact.phone_e164}</a>
                    </div>
                    <div>
                      <span>Адрес</span>
                      <strong>{contact.delivery_address}</strong>
                    </div>
                    <button
                      className="text-button"
                      type="button"
                      onClick={() =>
                        setContacts((current) => {
                          const next = { ...current };
                          delete next[task.task_id];
                          return next;
                        })
                      }
                    >
                      Скрыть контакт
                    </button>
                  </div>
                )}
                <div className="admin-order-actions">
                  {task.state === "assigned" && (
                    <button
                      className="button primary"
                      type="button"
                      disabled={busy}
                      onClick={() => void transition(task, "accept")}
                    >
                      Принять задание
                    </button>
                  )}
                  {(["accepted", "picked_up"] as CourierTask["state"][]).includes(task.state) &&
                    !contact && (
                      <button
                        className="button secondary"
                        type="button"
                        disabled={busy}
                        onClick={() => void revealContact(task)}
                      >
                        Показать контакт
                      </button>
                    )}
                  {task.state === "accepted" && (
                    <button
                      className="button primary"
                      type="button"
                      disabled={busy}
                      onClick={() => void transition(task, "pickup")}
                    >
                      Лампа получена
                    </button>
                  )}
                  {task.state === "picked_up" && (
                    <button
                      className="button primary"
                      type="button"
                      disabled={busy}
                      onClick={() => void transition(task, "deliver")}
                    >
                      Доставлено клиенту
                    </button>
                  )}
                </div>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}

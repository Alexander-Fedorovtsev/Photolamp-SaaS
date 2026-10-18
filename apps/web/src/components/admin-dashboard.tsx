"use client";

import { useCallback, useEffect, useState } from "react";
import {
  operationsDashboardSchema,
  type DashboardOrder,
  type OperationsDashboard,
} from "@/lib/admin";
import type { OrderFilter } from "@/components/admin-orders";

function formatDate(value: string, options?: Intl.DateTimeFormatOptions) {
  return new Intl.DateTimeFormat("ru-RU", {
    day: "numeric",
    month: "long",
    ...options,
    timeZone: "UTC",
  }).format(new Date(`${value}T12:00:00Z`));
}

function OrderRow({ order }: { order: DashboardOrder }) {
  const requestLabel = order.pending_request_types
    .map((kind) => (kind === "extension" ? "продление" : "возврат"))
    .join(" · ");
  return (
    <li>
      <div>
        <strong>{order.model_name}</strong>
        <span>
          {order.branch_name} · #{order.order_id.slice(0, 8)}
        </span>
      </div>
      <div className="dashboard-order-meta">
        <span>
          {formatDate(order.start_date, { month: "short" })} —{" "}
          {formatDate(order.end_date, { month: "short" })}
        </span>
        {order.overdue_days > 0 && <b>Просрочен на {order.overdue_days} дн.</b>}
        {requestLabel && <b>{requestLabel}</b>}
      </div>
    </li>
  );
}

export function AdminDashboard({
  tenantId,
  refreshKey,
  onUnauthorized,
  onAccessChanged,
  onOpenOrders,
}: {
  tenantId: string;
  refreshKey: number;
  onUnauthorized: () => void;
  onAccessChanged: () => void;
  onOpenOrders: (filter: OrderFilter) => void;
}) {
  const [dashboard, setDashboard] = useState<OperationsDashboard | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const loadDashboard = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const response = await fetch(
        `/api/admin/dashboard?tenant_id=${encodeURIComponent(tenantId)}`,
        { cache: "no-store" },
      );
      if (response.status === 401) {
        onUnauthorized();
        return;
      }
      const payload = (await response.json().catch(() => ({}))) as unknown;
      const detail = (payload as { detail?: unknown }).detail;
      if (response.status === 403 || (response.status === 404 && detail === "Tenant not found")) {
        setDashboard(null);
        onAccessChanged();
        return;
      }
      if (!response.ok) {
        throw new Error((payload as { detail?: string }).detail ?? "Не удалось загрузить сводку");
      }
      setDashboard(operationsDashboardSchema.parse(payload));
    } catch (loadError) {
      setDashboard(null);
      setError(loadError instanceof Error ? loadError.message : "Не удалось загрузить сводку");
    } finally {
      setLoading(false);
    }
  }, [onAccessChanged, onUnauthorized, tenantId]);

  useEffect(() => {
    // Tenant selection and completed order actions invalidate the server read model.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadDashboard();
  }, [loadDashboard, refreshKey]);

  if (loading && !dashboard) return <p className="admin-inline-loading">Собираем сводку дня…</p>;
  if (error) {
    return (
      <section className="admin-dashboard-error" aria-label="Операционная сводка">
        <p role="alert">{error}</p>
        <button className="button secondary" type="button" onClick={() => void loadDashboard()}>
          Повторить
        </button>
      </section>
    );
  }
  if (!dashboard) return null;

  const metrics: Array<{ label: string; value: number; filter: OrderFilter; urgent?: boolean }> = [
    { label: "Ждут подтверждения", value: dashboard.counts.awaiting_confirmation, filter: "held" },
    { label: "К выдаче", value: dashboard.counts.handovers_due, filter: "handovers" },
    { label: "Вернут сегодня", value: dashboard.counts.returns_today, filter: "returns_today" },
    { label: "Просрочены", value: dashboard.counts.overdue, filter: "overdue", urgent: true },
    { label: "Запросы клиентов", value: dashboard.counts.pending_requests, filter: "requests" },
    { label: "На осмотре", value: dashboard.counts.inspection, filter: "returned" },
  ];
  const groups = [
    { title: "Выдать", empty: "Выдач на сегодня нет", orders: dashboard.handovers_due },
    {
      title: "Вернуть сегодня",
      empty: "Возвратов на сегодня нет",
      orders: dashboard.returns_today,
    },
    {
      title: "Вернуть завтра",
      empty: "Возвратов на завтра нет",
      orders: dashboard.returns_tomorrow,
    },
    { title: "Требуют внимания", empty: "Срочных ситуаций нет", orders: dashboard.overdue },
  ];

  return (
    <section className="admin-dashboard" aria-labelledby="dashboard-title">
      <div className="admin-section-heading">
        <div>
          <p className="eyebrow">
            Сегодня · {formatDate(dashboard.local_date, { weekday: "long" })}
          </p>
          <h2 id="dashboard-title">Операционная сводка</h2>
          <p className="small">Сроки рассчитаны в часовом поясе каждого филиала.</p>
        </div>
        <button className="button secondary" type="button" onClick={() => void loadDashboard()}>
          Обновить
        </button>
      </div>

      <div className="dashboard-metrics" aria-label="Показатели заказов">
        {metrics.map((metric) => (
          <button
            className={metric.urgent && metric.value > 0 ? "is-urgent" : ""}
            type="button"
            key={metric.label}
            onClick={() => onOpenOrders(metric.filter)}
          >
            <strong>{metric.value}</strong>
            <span>{metric.label}</span>
          </button>
        ))}
      </div>

      <div className="dashboard-grid">
        {groups.map((group) => (
          <article className="dashboard-group" key={group.title}>
            <h3>{group.title}</h3>
            {group.orders.length ? (
              <ul>
                {group.orders.map((order) => (
                  <OrderRow order={order} key={order.order_id} />
                ))}
              </ul>
            ) : (
              <p className="dashboard-empty">{group.empty}</p>
            )}
          </article>
        ))}
      </div>

      <div className="dashboard-inventory" aria-label="Состояние парка">
        <div>
          <p className="eyebrow">Парк оборудования</p>
          <h3>{dashboard.inventory.available} доступно сейчас</h3>
        </div>
        <dl>
          <div>
            <dt>В аренде</dt>
            <dd>{dashboard.inventory.rented}</dd>
          </div>
          <div>
            <dt>На осмотре</dt>
            <dd>{dashboard.inventory.inspection}</dd>
          </div>
          <div>
            <dt>Обслуживание</dt>
            <dd>{dashboard.inventory.maintenance}</dd>
          </div>
          <div>
            <dt>Выведено</dt>
            <dd>{dashboard.inventory.retired}</dd>
          </div>
        </dl>
      </div>
    </section>
  );
}

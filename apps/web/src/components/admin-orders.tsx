"use client";

import { useCallback, useEffect, useState } from "react";
import {
  adminOrderPageSchema,
  adminOrderTransitionSchema,
  type AdminOrder,
  type AdminOrderPage,
  type AdminOrderState,
} from "@/lib/admin";

type OrderFilter = "all" | AdminOrderState;

const stateLabels: Record<AdminOrderState, string> = {
  held: "Ожидает подтверждения",
  confirmed: "Подтверждён",
  expired: "Истёк",
  cancelled: "Отменён",
};

const fulfillmentLabels = {
  pickup: "Самовывоз",
  delivery: "Доставка",
} as const;

function formatMoney(minor: number) {
  return new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency: "RUB",
    maximumFractionDigits: 0,
  }).format(minor / 100);
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("ru-RU", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${value}T12:00:00Z`));
}

function formatDateTime(value: string, timeZone: string) {
  return new Intl.DateTimeFormat("ru-RU", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    timeZone,
  }).format(new Date(value));
}

async function responsePayload(response: Response) {
  return (await response.json().catch(() => ({}))) as unknown;
}

export function AdminOrders({
  tenantId,
  timeZone,
  onUnauthorized,
}: {
  tenantId: string;
  timeZone: string;
  onUnauthorized: () => void;
}) {
  const [filter, setFilter] = useState<OrderFilter>("all");
  const [page, setPage] = useState<AdminOrderPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyOrderId, setBusyOrderId] = useState("");
  const [notice, setNotice] = useState("");

  const loadOrders = useCallback(async () => {
    setLoading(true);
    setNotice("");
    const query = new URLSearchParams({ tenant_id: tenantId });
    if (filter !== "all") query.set("state", filter);
    try {
      const response = await fetch(`/api/admin/orders?${query}`, { cache: "no-store" });
      if (response.status === 401) {
        onUnauthorized();
        return;
      }
      const payload = await responsePayload(response);
      if (!response.ok) {
        const detail = (payload as { detail?: string }).detail;
        throw new Error(detail ?? "Не удалось загрузить заказы");
      }
      setPage(adminOrderPageSchema.parse(payload));
    } catch (error) {
      setPage(null);
      setNotice(error instanceof Error ? error.message : "Не удалось загрузить заказы");
    } finally {
      setLoading(false);
    }
  }, [filter, onUnauthorized, tenantId]);

  useEffect(() => {
    // Tenant/filter changes are the external inputs for the protected queue fetch.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadOrders();
  }, [loadOrders]);

  async function transition(order: AdminOrder, action: "confirm" | "cancel") {
    const verb = action === "confirm" ? "подтвердить" : "отменить";
    if (!window.confirm(`Действительно ${verb} заказ ${order.order_id.slice(0, 8)}?`)) return;
    setBusyOrderId(order.order_id);
    setNotice("");
    try {
      const response = await fetch(
        `/api/admin/orders/${encodeURIComponent(order.order_id)}/${action}?tenant_id=${encodeURIComponent(tenantId)}`,
        {
          method: "POST",
          headers: { "Idempotency-Key": `admin-${action}-${crypto.randomUUID()}` },
        },
      );
      if (response.status === 401) {
        onUnauthorized();
        return;
      }
      const payload = await responsePayload(response);
      if (!response.ok) {
        const detail = (payload as { detail?: string }).detail;
        throw new Error(detail ?? "Не удалось изменить заказ");
      }
      adminOrderTransitionSchema.parse(payload);
      await loadOrders();
      setNotice(action === "confirm" ? "Заказ подтверждён." : "Заказ отменён.");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Не удалось изменить заказ");
    } finally {
      setBusyOrderId("");
    }
  }

  return (
    <section className="admin-section admin-orders" aria-labelledby="orders-title">
      <div className="admin-section-heading">
        <div>
          <p className="eyebrow">Операционная очередь</p>
          <h2 id="orders-title">Заказы</h2>
          <p className="small">
            Только tenant-scoped данные и неизменяемая стоимость на момент оформления.
          </p>
        </div>
        <div className="admin-order-toolbar">
          <label htmlFor="order-state-filter">Статус</label>
          <select
            id="order-state-filter"
            value={filter}
            onChange={(event) => setFilter(event.target.value as OrderFilter)}
          >
            <option value="all">Все</option>
            <option value="held">Ожидают подтверждения</option>
            <option value="confirmed">Подтверждены</option>
            <option value="expired">Истекли</option>
            <option value="cancelled">Отменены</option>
          </select>
          <button className="button secondary" type="button" onClick={() => void loadOrders()}>
            Обновить
          </button>
        </div>
      </div>

      {notice && (
        <p className="admin-alert" role="status" aria-live="polite">
          {notice}
        </p>
      )}
      {loading ? (
        <p className="admin-inline-loading">Загружаем заказы…</p>
      ) : !page ? null : page.items.length === 0 ? (
        <div className="admin-empty-state">
          <h3>В этой очереди пока нет заказов</h3>
          <p>Новые заявки появятся здесь после оформления через защищённый серверный контур.</p>
        </div>
      ) : (
        <div className="admin-order-list" aria-label={`Заказы: ${page.total}`}>
          {page.items.map((order) => (
            <OrderCard
              key={order.order_id}
              order={order}
              timeZone={timeZone}
              busy={busyOrderId === order.order_id}
              onTransition={transition}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function OrderCard({
  order,
  timeZone,
  busy,
  onTransition,
}: {
  order: AdminOrder;
  timeZone: string;
  busy: boolean;
  onTransition: (order: AdminOrder, action: "confirm" | "cancel") => Promise<void>;
}) {
  const canConfirm = order.state === "held" && order.identity_verified;
  const canCancel = order.state === "held" || order.state === "confirmed";
  const period = `${formatDate(order.snapshot.start_date)} — ${formatDate(order.snapshot.end_date)}`;

  return (
    <details className="admin-order-card">
      <summary>
        <div className="admin-order-summary-main">
          <span className={`status status-${order.state}`}>{stateLabels[order.state]}</span>
          <strong>{order.snapshot.model_name}</strong>
          <span>{period}</span>
        </div>
        <div className="admin-order-summary-side">
          <strong>{formatMoney(order.snapshot.quote.total_minor)}</strong>
          <span>#{order.order_id.slice(0, 8)}</span>
        </div>
      </summary>
      <div className="admin-order-detail">
        <dl>
          <div>
            <dt>Филиал</dt>
            <dd>{order.snapshot.branch_name}</dd>
          </div>
          <div>
            <dt>Получение</dt>
            <dd>{fulfillmentLabels[order.snapshot.fulfillment]}</dd>
          </div>
          <div>
            <dt>Источник</dt>
            <dd>Витрина сайта</dd>
          </div>
          <div>
            <dt>Клиент</dt>
            <dd>{order.identity_verified ? "Телефон подтверждён" : "Телефон не подтверждён"}</dd>
          </div>
          <div>
            <dt>Аренда</dt>
            <dd>{formatMoney(order.snapshot.quote.rental_amount_minor)}</dd>
          </div>
          <div>
            <dt>Залог</dt>
            <dd>{formatMoney(order.snapshot.quote.deposit_minor)}</dd>
          </div>
          <div>
            <dt>Создан</dt>
            <dd>{formatDateTime(order.created_at, timeZone)}</dd>
          </div>
          <div>
            <dt>Оплата</dt>
            <dd>Не подключена</dd>
          </div>
        </dl>
        {order.state === "held" && (
          <p className="small">Hold действует до {formatDateTime(order.expires_at, timeZone)}.</p>
        )}
        {order.state === "confirmed" && order.confirmed_at && (
          <p className="small">Подтверждён {formatDateTime(order.confirmed_at, timeZone)}.</p>
        )}
        <code className="admin-order-id">{order.order_id}</code>
        {canCancel && (
          <div className="admin-order-actions">
            {order.state === "held" && (
              <button
                className="button primary"
                type="button"
                disabled={busy || !canConfirm}
                title={
                  !order.identity_verified ? "Нужно подтверждение телефона клиента" : undefined
                }
                onClick={() => void onTransition(order, "confirm")}
              >
                {busy ? "Сохраняем…" : "Подтвердить"}
              </button>
            )}
            <button
              className="button secondary"
              type="button"
              disabled={busy}
              onClick={() => void onTransition(order, "cancel")}
            >
              Отменить заказ
            </button>
          </div>
        )}
        {order.state === "held" && !order.identity_verified && (
          <p className="admin-security-note">
            Подтверждение недоступно, пока клиент не подтвердил номер. Реальный SMS-провайдер не
            подключён.
          </p>
        )}
      </div>
    </details>
  );
}

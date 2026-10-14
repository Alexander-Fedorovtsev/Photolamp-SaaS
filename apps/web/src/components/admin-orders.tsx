"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  adminOrderContactAccessSchema,
  adminOrderPageSchema,
  adminOrderTransitionSchema,
  type AdminOrder,
  type AdminOrderContact,
  type AdminOrderPage,
  type AdminOrderState,
  type AdminCatalog,
} from "@/lib/admin";

type OrderFilter = "all" | AdminOrderState;

const stateLabels: Record<AdminOrderState, string> = {
  held: "Ожидает подтверждения",
  confirmed: "Подтверждён",
  in_progress: "У клиента",
  returned: "На осмотре",
  completed: "Завершён",
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
  catalog,
  onUnauthorized,
}: {
  tenantId: string;
  timeZone: string;
  catalog: AdminCatalog;
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

  async function transition(
    order: AdminOrder,
    action: "confirm" | "cancel" | "handover" | "return" | "inspection",
    outcome?: "passed" | "maintenance_required",
  ) {
    const verbs = {
      confirm: "подтвердить",
      cancel: "отменить",
      handover: order.snapshot.fulfillment === "delivery" ? "передать в доставку" : "выдать",
      return: "принять возврат",
      inspection:
        outcome === "passed" ? "завершить осмотр и освободить прибор" : "отправить прибор в ремонт",
    };
    const verb = verbs[action];
    if (!window.confirm(`Действительно ${verb} заказ ${order.order_id.slice(0, 8)}?`)) return;
    setBusyOrderId(order.order_id);
    setNotice("");
    try {
      const response = await fetch(
        `/api/admin/orders/${encodeURIComponent(order.order_id)}/${action}?tenant_id=${encodeURIComponent(tenantId)}`,
        {
          method: "POST",
          headers: {
            "Idempotency-Key": `admin-${action}-${crypto.randomUUID()}`,
            ...(action === "inspection" ? { "Content-Type": "application/json" } : {}),
          },
          body: action === "inspection" ? JSON.stringify({ outcome }) : undefined,
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
      const notices = {
        confirm: "Заказ подтверждён.",
        cancel: "Заказ отменён.",
        handover: "Передача отмечена. Прибор числится у клиента.",
        return: "Возврат принят. Прибор ожидает осмотра.",
        inspection:
          outcome === "passed"
            ? "Осмотр завершён. Прибор снова доступен."
            : "Осмотр завершён. Прибор переведён в обслуживание.",
      };
      setNotice(notices[action]);
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
            <option value="in_progress">У клиента</option>
            <option value="returned">На осмотре</option>
            <option value="completed">Завершены</option>
            <option value="expired">Истекли</option>
            <option value="cancelled">Отменены</option>
          </select>
          <button className="button secondary" type="button" onClick={() => void loadOrders()}>
            Обновить
          </button>
        </div>
      </div>

      <PhoneOrderForm
        tenantId={tenantId}
        timeZone={timeZone}
        catalog={catalog}
        onCreated={async () => {
          setFilter("all");
          await loadOrders();
          setNotice("Синтетический заказ по телефону создан и подтверждён.");
        }}
        onUnauthorized={onUnauthorized}
      />

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
              tenantId={tenantId}
              timeZone={timeZone}
              busy={busyOrderId === order.order_id}
              onTransition={transition}
              onUnauthorized={onUnauthorized}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function OrderCard({
  order,
  tenantId,
  timeZone,
  busy,
  onTransition,
  onUnauthorized,
}: {
  order: AdminOrder;
  tenantId: string;
  timeZone: string;
  busy: boolean;
  onTransition: (
    order: AdminOrder,
    action: "confirm" | "cancel" | "handover" | "return" | "inspection",
    outcome?: "passed" | "maintenance_required",
  ) => Promise<void>;
  onUnauthorized: () => void;
}) {
  const [contact, setContact] = useState<AdminOrderContact | null>(null);
  const [contactError, setContactError] = useState("");
  const [contactLoading, setContactLoading] = useState(false);
  const canConfirm = order.state === "held" && order.identity_verified;
  const canCancel = order.state === "held" || order.state === "confirmed";
  const period = `${formatDate(order.snapshot.start_date)} — ${formatDate(order.snapshot.end_date)}`;

  async function revealContact() {
    setContactLoading(true);
    setContactError("");
    try {
      const response = await fetch(
        `/api/admin/orders/${encodeURIComponent(order.order_id)}/contact?tenant_id=${encodeURIComponent(tenantId)}`,
        {
          method: "POST",
          headers: { "Idempotency-Key": `admin-contact-${crypto.randomUUID()}` },
        },
      );
      if (response.status === 401) {
        onUnauthorized();
        return;
      }
      const payload = await responsePayload(response);
      if (!response.ok) {
        const detail = (payload as { detail?: string }).detail;
        throw new Error(detail ?? "Контакт недоступен");
      }
      setContact(adminOrderContactAccessSchema.parse(payload).contact);
    } catch (error) {
      setContactError(error instanceof Error ? error.message : "Контакт недоступен");
    } finally {
      setContactLoading(false);
    }
  }

  return (
    <details
      className="admin-order-card"
      onToggle={(event) => {
        if (!event.currentTarget.open) {
          setContact(null);
          setContactError("");
        }
      }}
    >
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
            <dd>{order.source === "phone" ? "Заказ по телефону" : "Витрина сайта"}</dd>
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
        {order.state === "held" && order.expires_at && (
          <p className="small">Hold действует до {formatDateTime(order.expires_at, timeZone)}.</p>
        )}
        {order.state === "confirmed" && order.confirmed_at && (
          <p className="small">Подтверждён {formatDateTime(order.confirmed_at, timeZone)}.</p>
        )}
        {order.state === "in_progress" && order.handed_over_at && (
          <p className="small">Передан {formatDateTime(order.handed_over_at, timeZone)}.</p>
        )}
        {order.state === "returned" && order.returned_at && (
          <p className="small">Возвращён {formatDateTime(order.returned_at, timeZone)}.</p>
        )}
        {order.state === "completed" && order.completed_at && (
          <p className="small">
            Завершён {formatDateTime(order.completed_at, timeZone)} ·{" "}
            {order.inspection_outcome === "passed"
              ? "прибор готов к аренде"
              : "прибор направлен в обслуживание"}
          </p>
        )}
        <code className="admin-order-id">{order.order_id}</code>
        {order.source === "phone" &&
          (["confirmed", "in_progress", "returned"] as AdminOrderState[]).includes(order.state) && (
            <div className="admin-contact-access">
              {contact ? (
                <div className="admin-contact-panel">
                  <div>
                    <span>Получатель</span>
                    <strong>{contact.recipient_name}</strong>
                  </div>
                  <div>
                    <span>Телефон</span>
                    <a href={`tel:${contact.phone_e164}`}>{contact.phone_e164}</a>
                  </div>
                  {contact.delivery_address && (
                    <div>
                      <span>Адрес доставки</span>
                      <strong>{contact.delivery_address}</strong>
                    </div>
                  )}
                  <button className="text-button" type="button" onClick={() => setContact(null)}>
                    Скрыть контакт
                  </button>
                </div>
              ) : (
                <button
                  className="button secondary"
                  type="button"
                  disabled={contactLoading || busy}
                  onClick={() => void revealContact()}
                >
                  {contactLoading ? "Открываем…" : "Показать контакт"}
                </button>
              )}
              <p className="small">Каждое открытие контакта записывается в аудит.</p>
              {contactError && (
                <p className="admin-alert" role="alert">
                  {contactError}
                </p>
              )}
            </div>
          )}
        {(canCancel || ["in_progress", "returned"].includes(order.state)) && (
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
            {order.state === "confirmed" && (
              <button
                className="button primary"
                type="button"
                disabled={busy}
                onClick={() => void onTransition(order, "handover")}
              >
                {busy
                  ? "Сохраняем…"
                  : order.snapshot.fulfillment === "delivery"
                    ? "Передать в доставку"
                    : "Выдать клиенту"}
              </button>
            )}
            {canCancel && (
              <button
                className="button secondary"
                type="button"
                disabled={busy}
                onClick={() => void onTransition(order, "cancel")}
              >
                Отменить заказ
              </button>
            )}
            {order.state === "in_progress" && (
              <button
                className="button primary"
                type="button"
                disabled={busy}
                onClick={() => void onTransition(order, "return")}
              >
                {busy ? "Сохраняем…" : "Принять возврат"}
              </button>
            )}
            {order.state === "returned" && (
              <>
                <button
                  className="button primary"
                  type="button"
                  disabled={busy}
                  onClick={() => void onTransition(order, "inspection", "passed")}
                >
                  Осмотр пройден
                </button>
                <button
                  className="button secondary"
                  type="button"
                  disabled={busy}
                  onClick={() => void onTransition(order, "inspection", "maintenance_required")}
                >
                  Нужен ремонт
                </button>
              </>
            )}
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

function localIsoDate(timeZone: string, offsetDays: number) {
  const local = new Intl.DateTimeFormat("en-CA", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    timeZone,
  }).format(new Date());
  const date = new Date(`${local}T12:00:00Z`);
  date.setUTCDate(date.getUTCDate() + offsetDays);
  return date.toISOString().slice(0, 10);
}

function PhoneOrderForm({
  tenantId,
  timeZone,
  catalog,
  onCreated,
  onUnauthorized,
}: {
  tenantId: string;
  timeZone: string;
  catalog: AdminCatalog;
  onCreated: () => Promise<void>;
  onUnauthorized: () => void;
}) {
  const [fulfillment, setFulfillment] = useState<"pickup" | "delivery">("pickup");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");
  const publishedModels = useMemo(
    () =>
      catalog.models.filter(
        (model) =>
          model.publication_state === "published" &&
          catalog.pricing.some(
            (price) => price.equipment_model_id === model.id && price.state === "active",
          ),
      ),
    [catalog.models, catalog.pricing],
  );
  const activeBranches = useMemo(
    () =>
      catalog.branches.filter(
        (branch) =>
          branch.state === "active" &&
          (fulfillment === "pickup" ? branch.pickup_enabled : branch.delivery_enabled),
      ),
    [catalog.branches, fulfillment],
  );
  const startDate = localIsoDate(timeZone, 1);
  const endDate = localIsoDate(timeZone, 3);
  const available = publishedModels.length > 0 && activeBranches.length > 0;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setCreating(true);
    setError("");
    try {
      const response = await fetch(
        `/api/admin/orders/phone?tenant_id=${encodeURIComponent(tenantId)}`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": `admin-phone-${crypto.randomUUID()}`,
          },
          body: JSON.stringify({
            phone_e164: "+70000000000",
            recipient_name: "Тестовый клиент",
            delivery_address: fulfillment === "delivery" ? "Тестовый адрес" : null,
            model_id: data.get("model_id"),
            branch_id: data.get("branch_id"),
            start_date: data.get("start_date"),
            end_date: data.get("end_date"),
            fulfillment,
          }),
        },
      );
      if (response.status === 401) {
        onUnauthorized();
        return;
      }
      const payload = await responsePayload(response);
      if (!response.ok) {
        const detail = (payload as { detail?: string | { msg?: string }[] }).detail;
        const message = Array.isArray(detail) ? detail[0]?.msg : detail;
        throw new Error(message ?? "Не удалось создать заказ");
      }
      adminOrderTransitionSchema.parse(payload);
      await onCreated();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось создать заказ");
    } finally {
      setCreating(false);
    }
  }

  return (
    <details className="admin-phone-order">
      <summary className="button primary">Новый заказ по телефону</summary>
      <form onSubmit={(event) => void submit(event)}>
        <div className="admin-phone-order-heading">
          <div>
            <h3>Оформление оператором</h3>
            <p>Контакт и адрес шифруются. В demo разрешены только эти синтетические данные.</p>
          </div>
          <span className="status status-confirmed">SMS выключен</span>
        </div>
        <div className="admin-phone-order-grid">
          <label>
            Телефон
            <input value="+70000000000" readOnly aria-readonly="true" />
          </label>
          <label>
            Получатель
            <input value="Тестовый клиент" readOnly aria-readonly="true" />
          </label>
          <label>
            Модель
            <select name="model_id" required disabled={!available}>
              {publishedModels.map((model) => (
                <option key={model.id} value={model.id}>
                  {model.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Филиал
            <select name="branch_id" required disabled={!available}>
              {activeBranches.map((branch) => (
                <option key={branch.id} value={branch.id}>
                  {branch.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Начало
            <input
              name="start_date"
              type="date"
              min={startDate}
              defaultValue={startDate}
              required
            />
          </label>
          <label>
            Окончание
            <input name="end_date" type="date" min={startDate} defaultValue={endDate} required />
          </label>
          <label>
            Получение
            <select
              value={fulfillment}
              onChange={(event) => setFulfillment(event.target.value as "pickup" | "delivery")}
            >
              <option value="pickup">Самовывоз</option>
              <option value="delivery">Доставка</option>
            </select>
          </label>
          {fulfillment === "delivery" && (
            <label>
              Адрес
              <input value="Тестовый адрес" readOnly aria-readonly="true" />
            </label>
          )}
        </div>
        {!available && (
          <p className="admin-security-note">
            Нужны опубликованная модель и активный филиал с действующим тарифом.
          </p>
        )}
        {error && (
          <p className="admin-alert" role="alert">
            {error}
          </p>
        )}
        <button className="button primary" type="submit" disabled={creating || !available}>
          {creating ? "Проверяем доступность…" : "Создать подтверждённый заказ"}
        </button>
        <p className="small">
          Цена и свободный прибор проверяются сервером. Уведомление клиенту сейчас не отправляется.
        </p>
      </form>
    </details>
  );
}

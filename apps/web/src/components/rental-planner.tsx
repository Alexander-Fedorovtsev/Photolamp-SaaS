"use client";

import Link from "next/link";
import { validateRentalDates } from "@/lib/rental-dates";
import { displayError, uiError } from "@/lib/ui-error";
import { FormEvent, useId, useRef, useState } from "react";
import { quoteSchema, type Quote } from "@/lib/quote";
import type { CatalogItem, TenantCatalog } from "@/lib/tenant";

const money = new Intl.NumberFormat("ru-RU", {
  style: "currency",
  currency: "RUB",
  maximumFractionDigits: 0,
});

type Props = {
  city: string;
  tenantSlug?: string;
  branches?: TenantCatalog["branches"];
  items?: CatalogItem[];
};

export function RentalPlanner({ city, tenantSlug, branches = [], items = [] }: Props) {
  const id = useId();
  const revision = useRef(0);
  const [method, setMethod] = useState<"delivery" | "pickup">("delivery");
  const [modelId, setModelId] = useState(items[0]?.id ?? "");
  const [branchId, setBranchId] = useState(branches[0]?.id ?? "");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [quote, setQuote] = useState<Quote | null>(null);
  const [errorField, setErrorField] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const canCheck = Boolean(tenantSlug && modelId && branchId && startDate && endDate);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canCheck || !tenantSlug || loading) return;
    const attempt = ++revision.current;
    const model = items.find((item) => item.id === modelId);
    const branch = branches.find((item) => item.id === branchId);
    const validation =
      model && branch
        ? validateRentalDates(
            startDate,
            endDate,
            model.minimum_days,
            model.maximum_days,
            branch.timezone,
          )
        : null;
    setQuote(null);
    if (validation) {
      setErrorField(validation.field);
      setError(uiError({ detail: validation }));
      return;
    }
    setLoading(true);
    setError(null);
    setQuote(null);
    try {
      const response = await fetch(`/api/storefront/${encodeURIComponent(tenantSlug)}/quote`, {
        signal: AbortSignal.timeout(10000),
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model_id: modelId,
          branch_id: branchId,
          start_date: startDate,
          end_date: endDate,
          fulfillment: method,
        }),
      });
      const payload: unknown = await response.json();
      if (attempt !== revision.current) return;
      if (!response.ok) {
        setErrorField((payload as { detail?: { field?: string } }).detail?.field ?? "dates");
        throw new Error(
          uiError(
            payload,
            "Не удалось проверить даты. Проверьте выбранную модель и филиал или повторите попытку.",
          ),
        );
      }
      if (attempt === revision.current) setQuote(quoteSchema.parse(payload));
    } catch (reason) {
      if (attempt === revision.current)
        setError(
          displayError(
            reason,
            "Не удалось проверить даты. Проверьте соединение и повторите попытку.",
          ),
        );
    } finally {
      setLoading(false);
    }
  }

  function resetQuote() {
    revision.current += 1;
    setQuote(null);
    setError(null);
    setErrorField("");
  }

  if (!tenantSlug)
    return (
      <div className="planner">
        <div>
          <p className="eyebrow">Расчёт аренды · demo</p>
          <h2 id="rental-title">Выберите организацию</h2>
          <p>
            Откройте витрину своего города, затем выберите модель, филиал и даты. У каждой
            организации свои синтетические тарифы.
          </p>
        </div>
        <div className="planner-card demo-links">
          <Link className="button primary" href="/r/demo-ufa#rental">
            Уфа · Солнечный дом
          </Link>
          <Link className="button secondary" href="/r/demo-kazan#rental">
            Казань · Бережный свет
          </Link>
          <p className="small">
            Расчёт на витрине не резервирует прибор. Оформление и платежи пока выключены.
          </p>
        </div>
      </div>
    );

  return (
    <div className="planner">
      <div>
        <p className="eyebrow">Расчёт аренды · серверное демо</p>
        <h2 id="rental-title">
          Проверьте даты.
          <br />
          Увидьте стоимость заранее.
        </h2>
        <p>Сервис проверит доступные лампы и рассчитает аренду по тарифу организации.</p>
        <p className="small">
          Проверка не резервирует прибор. Каталог и цены в этой витрине синтетические и не являются
          офертой.
        </p>
      </div>
      <form className="planner-card" onSubmit={submit}>
        {(!items.length || !branches.length) && (
          <p role="status">
            Для расчёта нужны опубликованная модель с активным тарифом и работающий филиал.
            Обратитесь к организации позднее.
          </p>
        )}
        <p className="planner-city">{city} · демонстрационная витрина</p>
        <div className="planner-fields">
          {items.length > 0 && (
            <label className="planner-model" htmlFor={`${id}-model`}>
              Модель
              <select
                id={`${id}-model`}
                value={modelId}
                onChange={(event) => {
                  setModelId(event.target.value);
                  resetQuote();
                }}
              >
                {items.map((item) => (
                  <option value={item.id} key={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          {branches.length > 0 && (
            <label className="planner-model" htmlFor={`${id}-branch`}>
              Точка аренды
              <select
                id={`${id}-branch`}
                value={branchId}
                onChange={(event) => {
                  setBranchId(event.target.value);
                  resetQuote();
                }}
              >
                {branches.map((branch) => (
                  <option value={branch.id} key={branch.id}>
                    {branch.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label htmlFor={`${id}-start`}>
            Первый день
            <input
              id={`${id}-start`}
              type="date"
              aria-invalid={errorField === "start_date"}
              aria-describedby={errorField === "start_date" ? `${id}-date-error` : undefined}
              value={startDate}
              onChange={(event) => {
                setStartDate(event.target.value);
                resetQuote();
              }}
              required
            />
          </label>
          <label htmlFor={`${id}-end`}>
            Последний день
            <input
              id={`${id}-end`}
              type="date"
              aria-invalid={errorField === "end_date"}
              aria-describedby={errorField === "end_date" ? `${id}-date-error` : undefined}
              value={endDate}
              onChange={(event) => {
                setEndDate(event.target.value);
                resetQuote();
              }}
              required
            />
          </label>
          <label className="planner-model" htmlFor={`${id}-method`}>
            Способ получения
            <select
              id={`${id}-method`}
              value={method}
              onChange={(event) => {
                setMethod(event.target.value as "delivery" | "pickup");
                resetQuote();
              }}
            >
              <option value="delivery">Доставка</option>
              <option value="pickup">Самовывоз</option>
            </select>
          </label>
          {error && (
            <p className="planner-error" id={`${id}-date-error`} role="alert">
              {error}
            </p>
          )}
        </div>
        <button
          className="button secondary planner-check"
          type="submit"
          disabled={!canCheck || loading}
        >
          {loading ? "Проверяем…" : "Проверить даты и стоимость"}
        </button>
        <div className="planner-result" role="status" aria-live="polite" aria-atomic="true">
          {error ? (
            <span>Измените условия выше и повторите проверку.</span>
          ) : quote ? (
            <>
              <span>
                {quote.days} дн. · {quote.fulfillment === "delivery" ? "доставка" : "самовывоз"}
              </span>
              <strong>Аренда: {money.format(quote.rental_amount_minor / 100)}</strong>
              <span>
                На эти даты доступно: {quote.available_units}. Расчёт выполнен сервером, прибор не
                зарезервирован.
              </span>
            </>
          ) : (
            <span>Выберите даты, чтобы сервер проверил наличие и тариф.</span>
          )}
        </div>
        <div className="future-actions">
          <button className="button primary" type="button" disabled>
            Перейти к бронированию — скоро
          </button>
          <button className="button secondary" type="button" disabled>
            Заказать по телефону — скоро
          </button>
        </div>
        <p className="small">
          Заявки и персональные данные пока не принимаются. Следующий этап добавит безопасное
          публичное оформление после запуска сервиса.
        </p>
      </form>
    </div>
  );
}

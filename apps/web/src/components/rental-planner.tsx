"use client";

import { FormEvent, useId, useState } from "react";
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
  const [method, setMethod] = useState<"delivery" | "pickup">("delivery");
  const [modelId, setModelId] = useState(items[0]?.id ?? "");
  const [branchId, setBranchId] = useState(branches[0]?.id ?? "");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [quote, setQuote] = useState<Quote | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const canCheck = Boolean(tenantSlug && modelId && branchId && startDate && endDate);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canCheck || !tenantSlug) return;
    setLoading(true);
    setError(null);
    setQuote(null);
    try {
      const response = await fetch(`/api/storefront/${encodeURIComponent(tenantSlug)}/quote`, {
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
      if (!response.ok) {
        if (response.status === 409) throw new Error("На выбранные даты свободных ламп нет.");
        if (response.status === 422) throw new Error("Проверьте даты и условия выбранного тарифа.");
        throw new Error("Не удалось проверить даты.");
      }
      setQuote(quoteSchema.parse(payload));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось проверить даты.");
    } finally {
      setLoading(false);
    }
  }

  function resetQuote() {
    setQuote(null);
    setError(null);
  }

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
              min={startDate || undefined}
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
            <strong className="planner-error">{error}</strong>
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
          временное резервирование.
        </p>
      </form>
    </div>
  );
}

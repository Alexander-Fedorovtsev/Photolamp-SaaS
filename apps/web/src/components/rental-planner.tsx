"use client";

import { useId, useState } from "react";
import type { CatalogItem } from "@/lib/tenant";

const rubles = new Intl.NumberFormat("ru-RU", {
  style: "currency",
  currency: "RUB",
  maximumFractionDigits: 0,
});

export function RentalPlanner({ city, items = [] }: { city: string; items?: CatalogItem[] }) {
  const id = useId();
  const [days, setDays] = useState("7");
  const [method, setMethod] = useState("delivery");
  const [modelId, setModelId] = useState(items[0]?.id ?? "");
  const selected = items.find((item) => item.id === modelId);
  const demoTotal = selected ? (selected.amount_minor_per_day * Number(days)) / 100 : null;
  return (
    <div className="planner">
      <div>
        <p className="eyebrow">Планирование аренды · демо</p>
        <h2 id="rental-title">
          Понятные условия.
          <br />
          Без неожиданных расходов.
        </h2>
        <p>Выберите модель, длительность и способ получения, чтобы проверить будущий сценарий.</p>
        <p className="small">
          Это демонстрационный расчёт по синтетическому тарифу. Наличие на даты, доставка и итоговая
          стоимость не подтверждаются.
        </p>
      </div>
      <div className="planner-card">
        <p className="planner-city">{city} · демонстрационная витрина</p>
        <div className="planner-fields">
          {items.length > 0 && (
            <label className="planner-model" htmlFor={`${id}-model`}>
              Модель
              <select
                id={`${id}-model`}
                value={modelId}
                onChange={(event) => setModelId(event.target.value)}
              >
                {items.map((item) => (
                  <option value={item.id} key={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label htmlFor={`${id}-days`}>
            Длительность аренды
            <select id={`${id}-days`} value={days} onChange={(e) => setDays(e.target.value)}>
              <option value="3">3 дня</option>
              <option value="7">7 дней</option>
              <option value="14">14 дней</option>
            </select>
          </label>
          <label htmlFor={`${id}-method`}>
            Способ получения
            <select id={`${id}-method`} value={method} onChange={(e) => setMethod(e.target.value)}>
              <option value="delivery">Доставка</option>
              <option value="pickup">Самовывоз</option>
            </select>
          </label>
        </div>
        <div className="planner-result" role="status" aria-live="polite" aria-atomic="true">
          <span>
            {days} {days === "3" ? "дня" : "дней"} ·{" "}
            {method === "delivery" ? "доставка" : "самовывоз"}
          </span>
          <strong>
            {demoTotal === null
              ? "Тарифы пока не настроены"
              : `Демо-расчёт: ${rubles.format(demoTotal)}`}
          </strong>
          <span>
            {demoTotal === null
              ? "Итоговая стоимость появится после настройки условий арендодателя."
              : "Только аренда по учебному тарифу, без доставки. Это не оферта и не подтверждение наличия."}
          </span>
        </div>
        <div className="future-actions">
          <button className="button primary" disabled>
            Оформить аренду — скоро
          </button>
          <button className="button secondary" disabled>
            Заказать по телефону — скоро
          </button>
        </div>
        <p className="small">
          Телефон оператора появится здесь после подтверждения контактов. Заявки сейчас не
          принимаются.
        </p>
      </div>
    </div>
  );
}

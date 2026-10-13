"use client";

import { useId, useState } from "react";

export function RentalPlanner({ city }: { city: string }) {
  const id = useId();
  const [days, setDays] = useState("7");
  const [method, setMethod] = useState("delivery");
  return (
    <div className="planner">
      <div>
        <p className="eyebrow">Планирование аренды · демо</p>
        <h2 id="rental-title">
          Понятные условия.
          <br />
          Без неожиданных расходов.
        </h2>
        <p>Выберите длительность и способ получения, чтобы познакомиться с будущим оформлением.</p>
        <p className="small">
          Это пример интерфейса. Наличие ламп, доставка и стоимость ещё не подтверждаются.
        </p>
      </div>
      <div className="planner-card">
        <p className="planner-city">{city} · демонстрационная витрина</p>
        <div className="planner-fields">
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
          <strong>Тарифы пока не настроены</strong>
          <span>Итоговая стоимость появится после настройки условий арендодателя.</span>
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

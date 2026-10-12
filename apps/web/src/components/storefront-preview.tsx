import type { TenantPreview } from "@/lib/tenant";
import { BrandHeader } from "./brand-header";
import { LampScene } from "./lamp-scene";
import { ArrowIcon } from "./arrow-icon";

const steps = [
  ["Выберите даты", "Укажите, когда понадобится оборудование."],
  ["Подтвердите телефон", "В будущем — через SMS или с помощью оператора."],
  ["Согласуйте получение", "Условия доставки и комплект определит арендодатель."],
  ["Продлите или верните", "Управление арендой появится в личном кабинете."],
];

export function StorefrontPreview({ tenant }: { tenant: TenantPreview }) {
  return (
    <div data-accent={tenant.accent}>
      <BrandHeader name={tenant.display_name} city={tenant.city} />
      <main id="main-content">
        <div className="demo-notice" role="note">
          Демонстрационная витрина. Аренда пока недоступна; тарифы и контакты не настроены.
        </div>
        <section className="hero container" aria-labelledby="hero-title">
          <div>
            <p className="eyebrow">Фотолампа домой · {tenant.city}</p>
            <h1 id="hero-title">
              Больше спокойствия.
              <br />
              Меньше лишних забот.
            </h1>
            <p className="lead">
              Готовим удобный сервис аренды фотоламп: понятные условия, оформление онлайн или с
              помощью оператора.
            </p>
            <a className="button primary" href="#process">
              Как будет работать аренда <ArrowIcon />
            </a>
            <p className="medical-note">
              Использование оборудования — по назначению врача и инструкции производителя. Сервис не
              назначает лечение.
            </p>
          </div>
          <LampScene />
        </section>
        <section id="process" className="section container" aria-labelledby="process-title">
          <p className="eyebrow">Будущий сценарий</p>
          <h2 id="process-title">Всё понятно на каждом шаге</h2>
          <ol className="steps">
            {steps.map(([title, description], index) => (
              <li key={title} className="step-card">
                <span className="step-number" aria-hidden="true">
                  0{index + 1}
                </span>
                <h3>{title}</h3>
                <p>{description}</p>
              </li>
            ))}
          </ol>
        </section>
        <section className="section container" aria-labelledby="availability-title">
          <div className="availability-panel">
            <p className="eyebrow">Следующий этап</p>
            <h2 id="availability-title">Онлайн или по телефону — как вам удобнее</h2>
            <p>
              Оба способа будут работать с единым календарём оборудования. Сейчас это
              предварительный просмотр, без оформления заявки.
            </p>
            <div className="future-actions">
              <button className="button primary" disabled>
                Оформить аренду — скоро
              </button>
              <button className="button secondary" disabled>
                Заказать по телефону — скоро
              </button>
            </div>
            <p className="small">Цена появится после настройки тарифов арендодателя.</p>
          </div>
        </section>
      </main>
      <footer className="site-footer container">
        <span>{tenant.display_name}</span>
        <span>Демонстрация · без персональных данных</span>
      </footer>
    </div>
  );
}

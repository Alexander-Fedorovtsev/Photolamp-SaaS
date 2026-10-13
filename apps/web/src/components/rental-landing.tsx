import Link from "next/link";
import { BrandHeader } from "./brand-header";
import { LampScene } from "./lamp-scene";
import { ArrowIcon } from "./arrow-icon";
import { RentalPlanner } from "./rental-planner";
import { MotionControls } from "./motion-controls";
import { CatalogSection } from "./catalog-section";
import type { TenantCatalog } from "@/lib/tenant";

const steps = [
  [
    "Выберите даты",
    "Укажите, когда понадобится лампа. Сервис покажет доступность и условия аренды.",
  ],
  [
    "Оформите удобным способом",
    "Онлайн или с помощью оператора по телефону — оба способа будут доступны.",
  ],
  ["Согласуйте получение", "Уточните доставку или самовывоз, комплект и инструкции арендодателя."],
  [
    "Продлите или верните",
    "Запросите продление или согласуйте возврат. Продление требует проверки доступности.",
  ],
];
const benefits = [
  [
    "01",
    "Всё важное — перед глазами",
    "Даты, стоимость, комплект и условия в одном месте. Без поиска по перепискам.",
  ],
  [
    "02",
    "Можно поговорить с человеком",
    "Если удобнее по телефону, оператор поможет с организацией аренды.",
  ],
  [
    "03",
    "Вместе до возврата",
    "Получение, запрос продления и возврат — части одного понятного процесса.",
  ],
];
const questions = [
  [
    "Можно ли уже оформить аренду?",
    "Пока нет: перед вами демонстрация будущего сервиса. Онлайн-заказ, SMS и платежи ещё не подключены. Мы не принимаем заявки и не собираем персональные данные.",
  ],
  [
    "Можно ли заказать по телефону?",
    "Такой способ предусмотрен наравне с онлайн-заказом. Подтверждённый телефон и часы работы оператора появятся после настройки арендодателя.",
  ],
  [
    "Сколько стоит аренда?",
    "Стоимость зависит от тарифа организации, срока и способа получения. Тарифы пока не настроены — в демо нет вымышленных цен или окончательного расчёта.",
  ],
  [
    "Что входит в комплект?",
    "Точный состав, модель прибора и документы укажет арендодатель. До оформления важно ознакомиться с комплектом, инструкцией производителя и условиями передачи.",
  ],
  [
    "Как продлить аренду и вернуть лампу?",
    "В будущем можно будет отправить запрос из личного кабинета или обратиться к оператору. Продление подтверждается после проверки доступности; способ возврата согласовывается с арендодателем.",
  ],
  [
    "Кто определяет, нужна ли фотолампа?",
    "Только врач определяет необходимость и режим применения. Сервис организует аренду оборудования, не ставит диагноз и не назначает лечение. Следуйте назначению врача и инструкции производителя.",
  ],
];

export function RentalLanding({
  name,
  city,
  accent,
  catalog,
  showDemoLinks = false,
}: {
  name: string;
  city: string;
  accent: TenantCatalog["accent"];
  catalog?: TenantCatalog;
  showDemoLinks?: boolean;
}) {
  const faq = catalog
    ? questions.map(([question, answer]) =>
        question === "Сколько стоит аренда?"
          ? [
              question,
              "На карточках показаны синтетические демо-тарифы из базы данных. Они нужны для проверки каталога и интерфейса, не являются офертой и не подтверждают итоговую стоимость или наличие на выбранные даты.",
            ]
          : [question, answer],
      )
    : questions;
  return (
    <div data-accent={accent} className="landing">
      <BrandHeader name={name} city={city} landing />
      <div className="demo-notice" role="note">
        {catalog
          ? "Демонстрационная витрина. Каталог и цены синтетические; аренда, контакты и проверка доступности пока отключены."
          : "Демонстрационная витрина. Аренда пока недоступна; тарифы и контакты не настроены."}
      </div>
      <MotionControls>
        <main id="main-content">
          <section className="hero container" aria-labelledby="home-title">
            <div>
              <p className="eyebrow">Фотолампа домой · {city}</p>
              <h1 id="home-title">
                Меньше хлопот.
                <br />
                <span>Больше времени</span>
                <br />
                рядом.
              </h1>
              <p className="lead">
                Аренда фотолампы домой — с понятными условиями и заботой об организационных деталях.
                Готовим сервис, в котором удобно и онлайн, и по телефону.
              </p>
              <div className="hero-actions">
                <a href="#rental" className="button primary">
                  Посмотреть условия <ArrowIcon />
                </a>
                <a href="#process" className="text-link">
                  Как это будет работать
                </a>
              </div>
              <p className="medical-note">
                Использование — по назначению врача и инструкции производителя. Мы помогаем с
                арендой, а не назначаем лечение.
              </p>
            </div>
            <LampScene />
          </section>
          <section className="promise-strip container" aria-label="Будущий сервис">
            <span>Онлайн и по телефону</span>
            <span>Понятные условия</span>
            <span>От получения до возврата</span>
          </section>
          {catalog && <CatalogSection catalog={catalog} />}
          <section
            id="process"
            className="section container"
            aria-labelledby="process-title"
            data-reveal
          >
            <p className="eyebrow">Будущий сценарий · четыре шага</p>
            <h2 id="process-title">
              Организовать аренду проще,
              <br />
              когда есть понятный план.
            </h2>
            <ol className="steps">
              {steps.map(([title, description], index) => (
                <li className="step-card" key={title} data-reveal>
                  <span className="step-number" aria-hidden="true">
                    0{index + 1}
                  </span>
                  <h3>{title}</h3>
                  <p>{description}</p>
                </li>
              ))}
            </ol>
          </section>
          <section
            className="section benefits-section"
            aria-labelledby="benefits-title"
            data-reveal
          >
            <div className="container">
              <p className="eyebrow">Для спокойных повседневных дел</p>
              <h2 id="benefits-title">Забота — в деталях сервиса.</h2>
              <div className="benefit-grid">
                {benefits.map(([number, title, description]) => (
                  <article className="benefit-card" key={number}>
                    <span className="benefit-icon" aria-hidden="true">
                      {number}
                    </span>
                    <h3>{title}</h3>
                    <p>{description}</p>
                  </article>
                ))}
              </div>
              <p className="small">
                Эти возможности находятся в разработке. Сейчас доступен только предварительный
                просмотр.
              </p>
            </div>
          </section>
          <section
            id="rental"
            className="section container"
            aria-labelledby="rental-title"
            data-reveal
          >
            <RentalPlanner city={city} items={catalog?.items} />
          </section>
          <section
            className="section container care-section"
            aria-labelledby="care-title"
            data-reveal
          >
            <div className="care-symbol" aria-hidden="true">
              ♡
            </div>
            <div>
              <p className="eyebrow">Дома, в привычном ритме</p>
              <h2 id="care-title">
                Организационные вопросы — сервису.
                <br />
                Время рядом — вам.
              </h2>
              <p>
                Перед получением будут доступны условия аренды, состав комплекта и порядок возврата.
                Инструкции по использованию предоставляет производитель, медицинские рекомендации —
                ваш врач.
              </p>
              <p className="small">
                Подтверждённые документы, контакты и режим работы ещё предстоит добавить.
              </p>
            </div>
          </section>
          <section
            id="questions"
            className="section container faq-section"
            aria-labelledby="faq-title"
            data-reveal
          >
            <div>
              <p className="eyebrow">Ответы без сложностей</p>
              <h2 id="faq-title">Возможно, вы хотели спросить.</h2>
            </div>
            <div className="faq-list">
              {faq.map(([question, answer]) => (
                <details key={question}>
                  <summary>{question}</summary>
                  <p>{answer}</p>
                </details>
              ))}
            </div>
          </section>
          <section className="section container" aria-labelledby="bottom-title" data-reveal>
            <div className="bottom-cta">
              <p className="eyebrow">Один шаг к понятному сервису</p>
              <h2 id="bottom-title">
                Фотолампа домой.
                <br />
                Без лишних организационных забот.
              </h2>
              <p>
                Познакомьтесь с будущими условиями аренды. Оформление станет доступно после запуска
                сервиса.
              </p>
              <a className="button primary" href="#rental">
                Посмотреть условия <ArrowIcon />
              </a>
            </div>
          </section>
          {showDemoLinks && (
            <section
              id="landlords"
              className="section container foundation-note"
              aria-labelledby="foundation-title"
            >
              <p className="eyebrow">Для арендодателей · платформа в разработке</p>
              <h2 id="foundation-title">Ваш бренд. Ваш сервис.</h2>
              <p>
                У каждой организации — своя витрина, оборудование и условия. Онлайн-заказы и заказы
                по телефону будут частью одной системы.
              </p>
              <div className="demo-links">
                <Link className="text-link" href="/r/demo-ufa">
                  Посмотреть демо в Уфе
                </Link>
                <Link className="text-link" href="/r/demo-kazan">
                  Другая организация · демо в Казани
                </Link>
              </div>
            </section>
          )}
        </main>
      </MotionControls>
      <footer className="site-footer container">
        <span>
          {name} · {city}
        </span>
        <span>Демонстрация · без персональных данных</span>
        <a href="#questions">Вопросы об аренде</a>
      </footer>
      <nav className="mobile-cta" aria-label="Быстрые действия">
        <a className="button primary" href="#rental">
          Условия аренды
        </a>
        <a className="button secondary" href="#questions">
          Вопросы
        </a>
      </nav>
    </div>
  );
}

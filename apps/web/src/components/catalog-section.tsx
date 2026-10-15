import type { TenantCatalog } from "@/lib/tenant";

const illustrations = {
  floor: "☀",
  compact: "◒",
  mobile: "✦",
} as const;

const rubles = new Intl.NumberFormat("ru-RU", {
  style: "currency",
  currency: "RUB",
  maximumFractionDigits: 0,
});

function days(value: number) {
  const mod100 = value % 100;
  const mod10 = value % 10;
  if (mod100 >= 11 && mod100 <= 14) return `${value} дней`;
  if (mod10 === 1) return `${value} день`;
  if (mod10 >= 2 && mod10 <= 4) return `${value} дня`;
  return `${value} дней`;
}

export function CatalogSection({ catalog }: { catalog: TenantCatalog }) {
  return (
    <section id="catalog" className="section container" aria-labelledby="catalog-title" data-reveal>
      <div className="catalog-heading">
        <div>
          <p className="eyebrow">Каталог организации · данные из PostgreSQL</p>
          <h2 id="catalog-title">Выберите подходящий формат лампы.</h2>
        </div>
        <p className="catalog-disclaimer">{catalog.public_note}</p>
      </div>
      <div className="catalog-grid">
        {catalog.items.map((item) => (
          <article className="catalog-card" key={item.id}>
            <div
              className="catalog-illustration"
              data-variant={item.illustration}
              aria-hidden="true"
            >
              <span>{illustrations[item.illustration]}</span>
            </div>
            <div className="catalog-card-body">
              <p className="catalog-label">Фототерапевтическая лампа</p>
              <h3>{item.name}</h3>
              {item.manufacturer && (
                <p className="catalog-manufacturer">Производитель: {item.manufacturer}</p>
              )}
              <p>{item.description}</p>
              <p className="catalog-kit">
                <strong>Комплект:</strong> {item.included_items.join(", ")}.
              </p>
              <dl className="catalog-facts">
                <div>
                  <dt>{item.price_is_demo ? "Демо-тариф" : "Тариф"}</dt>
                  <dd>от {rubles.format(item.amount_minor_per_day / 100)} / день</dd>
                </div>
                <div>
                  <dt>Срок аренды</dt>
                  <dd>
                    {days(item.minimum_days)}–{days(item.maximum_days)}
                  </dd>
                </div>
                <div>
                  <dt>Залог</dt>
                  <dd>
                    {item.deposit_minor === 0
                      ? "Без залога"
                      : rubles.format(item.deposit_minor / 100)}
                  </dd>
                </div>
              </dl>
              <p className="inventory-note">
                Учётный статус: {item.serviceable_units} из {item.listed_units} приборов в статусе
                «доступен». Точная доступность проверяется на выбранные даты.
              </p>
            </div>
          </article>
        ))}
      </div>
      <p className="small catalog-source">
        Витрина не раскрывает инвентарные коды, серийные номера или служебные статусы отдельных
        приборов.
      </p>
    </section>
  );
}

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
              <p>{item.description}</p>
              <p className="catalog-kit">
                <strong>Демо-комплект:</strong> {item.included_items.join(", ")}.
              </p>
              <dl className="catalog-facts">
                <div>
                  <dt>Демо-тариф</dt>
                  <dd>от {rubles.format(item.amount_minor_per_day / 100)} / день</dd>
                </div>
                <div>
                  <dt>Минимальный срок</dt>
                  <dd>{item.minimum_days} дня</dd>
                </div>
              </dl>
              <p className="inventory-note">
                Учётный статус: {item.serviceable_units} из {item.listed_units} без сервисной
                блокировки. Доступность на даты пока не рассчитывается.
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

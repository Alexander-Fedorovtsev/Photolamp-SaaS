import Link from "next/link";

export function BrandHeader({
  name = "Photolamp",
  city,
  landing = false,
  tenantSlug,
}: {
  name?: string;
  city?: string;
  landing?: boolean;
  tenantSlug?: string;
}) {
  return (
    <header className="site-header">
      <Link href="/" className="brand" aria-label="Photolamp — главная">
        <span className="brand-mark" aria-hidden="true">
          ☀
        </span>
        <span>{name}</span>
      </Link>
      <span className="header-note">{city ? `${city} · демонстрация` : "Сервис с заботой"}</span>
      {landing && (
        <nav aria-label="Навигация по странице" className="header-nav">
          <a href="#process">Как это работает</a>
          <a href="#questions">Вопросы</a>
          <Link href={tenantSlug ? `/admin?tenant=${encodeURIComponent(tenantSlug)}` : "/admin"}>
            Для арендодателя
          </Link>
          <a href="#rental" className="button primary">
            Условия аренды
          </a>
        </nav>
      )}
    </header>
  );
}

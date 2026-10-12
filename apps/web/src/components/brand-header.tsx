import Link from "next/link";

export function BrandHeader({ name = "Photolamp", city }: { name?: string; city?: string }) {
  return (
    <header className="site-header">
      <Link href="/" className="brand" aria-label="Photolamp — главная">
        <span className="brand-mark" aria-hidden="true">
          ☀
        </span>
        <span>{name}</span>
      </Link>
      <span className="header-note">{city ? `${city} · демонстрация` : "Сервис с заботой"}</span>
    </header>
  );
}

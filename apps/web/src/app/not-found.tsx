import Link from "next/link";

export default function NotFound() {
  return (
    <main id="main-content" className="container status-page">
      <h1>Витрина не найдена</h1>
      <p>Проверьте адрес выбранной организации.</p>
      <Link href="/" className="button primary">
        На главную
      </Link>
    </main>
  );
}

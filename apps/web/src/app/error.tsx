"use client";

export default function ErrorPage({ reset }: { reset: () => void }) {
  return (
    <main id="main-content" className="container status-page">
      <h1>Витрина временно недоступна</h1>
      <p>Не удалось загрузить данные. Попробуйте ещё раз чуть позже.</p>
      <button className="button primary" onClick={reset}>
        Повторить загрузку
      </button>
    </main>
  );
}

"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";

const tokenPattern = /^pli_[A-Za-z0-9_-]{32,128}$/;

export function StaffInvitationSetup() {
  const [token, setToken] = useState("");
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");

  useEffect(() => {
    const candidate = new URLSearchParams(window.location.hash.slice(1)).get("token") ?? "";
    // The token exists only in the client-side fragment and is intentionally read after mount.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (tokenPattern.test(candidate)) setToken(candidate);
    window.history.replaceState(null, "", window.location.pathname);
    setReady(true);
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const password = String(data.get("password"));
    if (password !== String(data.get("password_confirmation"))) {
      setNotice("Пароли не совпадают.");
      return;
    }
    setBusy(true);
    setNotice("");
    try {
      const response = await fetch("/api/admin/invitations/redeem", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          invitation_token: token,
          login_name: String(data.get("login_name")),
          password,
        }),
      });
      const payload = (await response.json().catch(() => ({}))) as { detail?: string };
      if (!response.ok) throw new Error(payload.detail ?? "Не удалось принять приглашение");
      window.location.replace("/admin");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Не удалось принять приглашение");
      setBusy(false);
    }
  }

  return (
    <div className="admin-shell admin-login-shell">
      <header className="admin-public-header">
        <Link href="/" className="brand">
          <span className="brand-mark" aria-hidden="true">
            ☀
          </span>
          <span>Photolamp</span>
        </Link>
        <Link href="/admin" className="text-link">
          Вход сотрудника
        </Link>
      </header>
      <main id="main-content" className="admin-login-card">
        <p className="eyebrow">Приглашение в команду</p>
        <h1>Создайте защищённый вход.</h1>
        {!ready ? (
          <p>Проверяем ссылку…</p>
        ) : !token ? (
          <p className="admin-alert" role="alert">
            В ссылке нет действительного токена приглашения. Попросите владельца создать новую.
          </p>
        ) : (
          <form className="admin-auth-form" onSubmit={(event) => void submit(event)}>
            <label>
              Логин
              <input
                name="login_name"
                required
                minLength={4}
                maxLength={32}
                pattern="[a-z][a-z0-9._-]{3,31}"
                autoCapitalize="none"
                autoCorrect="off"
                autoComplete="username"
                aria-describedby="login-help"
              />
            </label>
            <p id="login-help" className="small">
              Латинские буквы, цифры, точка, дефис или подчёркивание. Начните с буквы.
            </p>
            <label>
              Пароль
              <input
                name="password"
                type="password"
                required
                minLength={12}
                maxLength={128}
                autoComplete="new-password"
              />
            </label>
            <label>
              Повторите пароль
              <input
                name="password_confirmation"
                type="password"
                required
                minLength={12}
                maxLength={128}
                autoComplete="new-password"
              />
            </label>
            <button className="button primary" type="submit" disabled={busy}>
              {busy ? "Подключаем…" : "Принять приглашение"}
            </button>
          </form>
        )}
        <p className="admin-security-note">
          Ссылка одноразовая. Токен удаляется из адресной строки и не сохраняется как пароль.
          Photolamp пока не отправляет SMS и не восстанавливает доступ автоматически.
        </p>
        {notice && (
          <p className="admin-alert" role="alert">
            {notice}
          </p>
        )}
      </main>
    </div>
  );
}

"use client";

import Link from "next/link";
import { displayError } from "@/lib/ui-error";
import { FormEvent, useEffect, useRef, useState } from "react";

const tokenPattern = /^pli_[A-Za-z0-9_-]{32,128}$/;

export function StaffInvitationSetup() {
  const candidateRef = useRef<string | null>(null);
  const [preview, setPreview] = useState<{
    organization_name: string;
    display_label: string;
    role: string;
    expires_at: string;
  } | null>(null);
  const [token, setToken] = useState("");
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");

  useEffect(() => {
    const candidate =
      candidateRef.current ?? new URLSearchParams(window.location.hash.slice(1)).get("token") ?? "";
    candidateRef.current = candidate;
    window.history.replaceState(null, "", window.location.pathname);
    if (!tokenPattern.test(candidate)) {
      // Bootstrap follows the fragment, which is unavailable during SSR.
      setReady(true);
      return;
    }
    let active = true;
    void (async () => {
      try {
        const response = await fetch("/api/admin/invitations/preview", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ invitation_token: candidate }),
          signal: AbortSignal.timeout(10000),
          cache: "no-store",
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail);
        if (active) {
          setToken(candidate);
          setPreview(payload);
        }
      } catch (error) {
        if (active) setNotice(displayError(error, "Не удалось проверить ссылку."));
      } finally {
        if (active) setReady(true);
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !preview || !token) return;
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
        signal: AbortSignal.timeout(10000),
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          invitation_token: token,
          login_name: String(data.get("login_name")),
          password,
        }),
      });
      const payload = (await response.json().catch(() => ({}))) as { detail?: string };
      if (!response.ok) {
        if (response.status === 409) {
          // A unique-login conflict does not consume a valid invitation.
          const checked = await fetch("/api/admin/invitations/preview", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ invitation_token: token }),
            signal: AbortSignal.timeout(10000),
            cache: "no-store",
          });
          if (!checked.ok) {
            setToken("");
            setPreview(null);
          } else
            throw new Error("Этот логин недоступен. Выберите другой логин и повторите попытку.");
        }
        throw new Error(payload.detail ?? "Не удалось принять приглашение");
      }
      const channel = new BroadcastChannel("photolamp-staff-session");
      channel.postMessage("changed");
      channel.close();
      window.location.replace("/admin");
    } catch (error) {
      setNotice(displayError(error, "Не удалось принять приглашение"));
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
        ) : !token || !preview ? (
          <p className="admin-alert" role="alert">
            В ссылке нет действительного токена приглашения. Попросите владельца создать новую.
          </p>
        ) : (
          <>
            <dl className="invitation-context">
              <dt>Организация</dt>
              <dd>{preview.organization_name}</dd>
              <dt>Сотрудник</dt>
              <dd>{preview.display_label}</dd>
              <dt>Роль</dt>
              <dd>
                {{
                  owner: "Владелец",
                  admin: "Администратор",
                  operator: "Оператор",
                  courier: "Курьер",
                }[preview.role] ?? "Сотрудник"}
              </dd>
              <dt>Ссылка действует до</dt>
              <dd>
                {new Intl.DateTimeFormat("ru-RU", {
                  dateStyle: "medium",
                  timeStyle: "short",
                }).format(new Date(preview.expires_at))}
              </dd>
            </dl>
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
          </>
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

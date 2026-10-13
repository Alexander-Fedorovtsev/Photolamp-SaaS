"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { AdminOrders } from "@/components/admin-orders";
import {
  adminCatalogSchema,
  membershipSchema,
  type AdminCatalog,
  type Membership,
} from "@/lib/admin";

type SessionState = "loading" | "anonymous" | "authenticated" | "error";

async function apiRequest(path: string, init?: RequestInit) {
  const response = await fetch(path, {
    ...init,
    headers: init?.body ? { "Content-Type": "application/json", ...init.headers } : init?.headers,
  });
  const payload = (await response.json().catch(() => ({}))) as { detail?: string };
  if (!response.ok) throw new Error(payload.detail ?? "Не удалось выполнить запрос");
  return payload;
}

function rubles(minor: number) {
  return new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency: "RUB",
    maximumFractionDigits: 0,
  }).format(minor / 100);
}

export function AdminWorkspace() {
  const [session, setSession] = useState<SessionState>("loading");
  const [memberships, setMemberships] = useState<Membership[]>([]);
  const [tenantId, setTenantId] = useState("");
  const [catalog, setCatalog] = useState<AdminCatalog | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");

  const clearSession = useCallback(() => {
    setCatalog(null);
    setMemberships([]);
    setTenantId("");
    setSession("anonymous");
  }, []);

  const loadSession = useCallback(async () => {
    try {
      const response = await fetch("/api/admin/session", { cache: "no-store" });
      if (response.status === 401) {
        setSession("anonymous");
        return;
      }
      if (!response.ok) throw new Error("Не удалось проверить сессию");
      const data = membershipSchema.array().parse(await response.json());
      setMemberships(data);
      setTenantId((current) => current || data[0]?.tenant_id || "");
      setSession("authenticated");
    } catch {
      setSession("error");
    }
  }, []);

  const loadCatalog = useCallback(async (selectedTenant: string) => {
    if (!selectedTenant) return;
    const response = await fetch(
      `/api/admin/catalog?tenant_id=${encodeURIComponent(selectedTenant)}`,
      {
        cache: "no-store",
      },
    );
    if (response.status === 401) {
      setSession("anonymous");
      setCatalog(null);
      return;
    }
    if (!response.ok) throw new Error("Не удалось загрузить каталог");
    setCatalog(adminCatalogSchema.parse(await response.json()));
  }, []);

  useEffect(() => {
    // Session state is intentionally bootstrapped from the same-origin server boundary.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadSession();
  }, [loadSession]);

  useEffect(() => {
    if (session === "authenticated" && tenantId) {
      // Tenant selection is the external input for the protected catalog fetch.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      void loadCatalog(tenantId).catch((error: unknown) => {
        setNotice(error instanceof Error ? error.message : "Ошибка загрузки");
      });
    }
  }, [loadCatalog, session, tenantId]);

  async function login() {
    setBusy(true);
    setNotice("");
    try {
      await apiRequest("/api/admin/session", { method: "POST" });
      await loadSession();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Демо-вход недоступен");
    } finally {
      setBusy(false);
    }
  }

  async function logout() {
    setBusy(true);
    await fetch("/api/admin/session", { method: "DELETE" });
    setCatalog(null);
    setMemberships([]);
    setTenantId("");
    setSession("anonymous");
    setBusy(false);
  }

  async function mutate(resource: string, body: object, id?: string) {
    setBusy(true);
    setNotice("");
    try {
      const suffix = id ? `/${encodeURIComponent(id)}` : "";
      await apiRequest(
        `/api/admin/catalog/${resource}${suffix}?tenant_id=${encodeURIComponent(tenantId)}`,
        { method: id ? "PATCH" : "POST", body: JSON.stringify(body) },
      );
      await loadCatalog(tenantId);
      setNotice("Изменение сохранено в demo-каталоге.");
      return true;
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Не удалось сохранить изменение");
      return false;
    } finally {
      setBusy(false);
    }
  }

  if (session === "loading") {
    return (
      <main id="main-content" className="admin-loading">
        Проверяем защищённую сессию…
      </main>
    );
  }

  if (session === "anonymous" || session === "error") {
    return (
      <div className="admin-shell admin-login-shell">
        <header className="admin-public-header">
          <Link href="/" className="brand">
            <span className="brand-mark" aria-hidden="true">
              ☀
            </span>
            <span>Photolamp</span>
          </Link>
          <Link href="/" className="text-link">
            Вернуться на витрину
          </Link>
        </header>
        <main id="main-content" className="admin-login-card">
          <p className="eyebrow">Панель арендодателя</p>
          <h1>Управляйте каталогом без работы с базой.</h1>
          <p className="lead">
            В этой версии доступен изолированный demo-кабинет: очередь заказов, филиалы, модели,
            экземпляры и тарифы. Все данные синтетические.
          </p>
          <button
            className="button primary"
            type="button"
            onClick={() => void login()}
            disabled={busy}
          >
            {busy ? "Открываем…" : "Войти в demo-панель"}
          </button>
          <p className="admin-security-note">
            Настоящие SMS, телефоны сотрудников и клиентские данные не используются. Demo-вход
            отключается в production.
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

  const currentTenant = memberships.find((tenant) => tenant.tenant_id === tenantId);
  return (
    <div className="admin-shell">
      <header className="admin-header">
        <Link href="/" className="brand">
          <span className="brand-mark" aria-hidden="true">
            ☀
          </span>
          <span>Photolamp</span>
        </Link>
        <div className="admin-tenant-picker">
          <label htmlFor="tenant-select">Организация</label>
          <select
            id="tenant-select"
            value={tenantId}
            onChange={(event) => setTenantId(event.target.value)}
          >
            {memberships.map((tenant) => (
              <option key={tenant.tenant_id} value={tenant.tenant_id}>
                {tenant.display_name}
              </option>
            ))}
          </select>
        </div>
        <button
          className="button secondary admin-logout"
          type="button"
          onClick={() => void logout()}
          disabled={busy}
        >
          Выйти
        </button>
      </header>

      <main id="main-content" className="admin-main">
        <section className="admin-intro">
          <div>
            <p className="eyebrow">Рабочее пространство · demo</p>
            <h1>{currentTenant?.display_name ?? "Каталог"}</h1>
            <p>
              Роль: {currentTenant?.role}. Заказы и каталог изолированы в выбранной организации.
            </p>
          </div>
          <Link href={`/r/${currentTenant?.slug ?? "demo-ufa"}`} className="button secondary">
            Открыть витрину
          </Link>
        </section>

        {notice && (
          <p className="admin-alert" role="status" aria-live="polite">
            {notice}
          </p>
        )}
        {!catalog || catalog.tenant_id !== tenantId ? (
          <p className="admin-loading">Загружаем tenant-каталог…</p>
        ) : (
          <>
            <section className="admin-metrics" aria-label="Сводка каталога">
              <article>
                <strong>{catalog.models.length}</strong>
                <span>модели</span>
              </article>
              <article>
                <strong>{catalog.assets.length}</strong>
                <span>приборы</span>
              </article>
              <article>
                <strong>
                  {catalog.assets.filter((asset) => asset.state === "available").length}
                </strong>
                <span>доступны</span>
              </article>
              <article>
                <strong>{catalog.branches.length}</strong>
                <span>филиалы</span>
              </article>
            </section>

            <AdminOrders
              tenantId={tenantId}
              timeZone={currentTenant?.timezone ?? "UTC"}
              onUnauthorized={clearSession}
            />

            <ModelSection catalog={catalog} busy={busy} mutate={mutate} />
            <AssetSection catalog={catalog} busy={busy} mutate={mutate} />
            <PricingSection catalog={catalog} busy={busy} mutate={mutate} />
            <BranchSection catalog={catalog} busy={busy} mutate={mutate} />
          </>
        )}
      </main>
    </div>
  );
}

type SectionProps = {
  catalog: AdminCatalog;
  busy: boolean;
  mutate: (resource: string, body: object, id?: string) => Promise<boolean>;
};

function ModelSection({ catalog, busy, mutate }: SectionProps) {
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const saved = await mutate("models", {
      name: data.get("name"),
      description: data.get("description"),
      included_items: String(data.get("included_items"))
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean),
      illustration: data.get("illustration"),
      publication_state: "draft",
    });
    if (saved) form.reset();
  }
  return (
    <section className="admin-section" aria-labelledby="models-title">
      <div className="admin-section-heading">
        <div>
          <p className="eyebrow">Каталог</p>
          <h2 id="models-title">Модели оборудования</h2>
        </div>
        <details className="admin-create">
          <summary className="button primary">Добавить модель</summary>
          <form onSubmit={(event) => void submit(event)}>
            <label>
              Название
              <input name="name" required minLength={1} maxLength={120} />
            </label>
            <label>
              Описание
              <textarea name="description" required maxLength={500} />
            </label>
            <label>
              Комплект через запятую
              <input
                name="included_items"
                required
                defaultValue="лампа, кейс, инструкция производителя"
              />
            </label>
            <label>
              Иллюстрация
              <select name="illustration">
                <option value="floor">Напольная</option>
                <option value="compact">Компактная</option>
                <option value="mobile">Мобильная</option>
              </select>
            </label>
            <button className="button primary" disabled={busy}>
              Сохранить черновик
            </button>
          </form>
        </details>
      </div>
      <div className="admin-card-grid">
        {catalog.models.map((model) => (
          <article className="admin-card" key={model.id}>
            <span className={`status status-${model.publication_state}`}>
              {model.publication_state === "published"
                ? "Опубликована"
                : model.publication_state === "draft"
                  ? "Черновик"
                  : "Архив"}
            </span>
            <h3>{model.name}</h3>
            <p>{model.description}</p>
            <p className="small">Комплект: {model.included_items.join(", ")}</p>
            <button
              className="text-button"
              disabled={busy}
              onClick={() =>
                void mutate(
                  "models",
                  {
                    publication_state:
                      model.publication_state === "published" ? "draft" : "published",
                  },
                  model.id,
                )
              }
            >
              {model.publication_state === "published" ? "Снять с витрины" : "Опубликовать"}
            </button>
          </article>
        ))}
      </div>
    </section>
  );
}

function AssetSection({ catalog, busy, mutate }: SectionProps) {
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const saved = await mutate("assets", {
      asset_code: data.get("asset_code"),
      branch_id: data.get("branch_id"),
      equipment_model_id: data.get("equipment_model_id"),
      state: "available",
    });
    if (saved) form.reset();
  }
  return (
    <section className="admin-section" aria-labelledby="assets-title">
      <div className="admin-section-heading">
        <div>
          <p className="eyebrow">Инвентарь</p>
          <h2 id="assets-title">Экземпляры</h2>
        </div>
        <details className="admin-create">
          <summary className="button primary">Добавить прибор</summary>
          <form onSubmit={(event) => void submit(event)}>
            <label>
              Внутренний код
              <input
                name="asset_code"
                required
                pattern="[A-Z0-9][A-Z0-9-]{1,31}"
                placeholder="DEMO-UFA-04"
              />
            </label>
            <label>
              Модель
              <select name="equipment_model_id" required>
                {catalog.models.map((model) => (
                  <option key={model.id} value={model.id}>
                    {model.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Филиал
              <select name="branch_id" required>
                {catalog.branches.map((branch) => (
                  <option key={branch.id} value={branch.id}>
                    {branch.name}
                  </option>
                ))}
              </select>
            </label>
            <button
              className="button primary"
              disabled={busy || !catalog.models.length || !catalog.branches.length}
            >
              Добавить
            </button>
          </form>
        </details>
      </div>
      <div className="admin-list">
        {catalog.assets.map((asset) => (
          <article key={asset.id}>
            <div>
              <strong>{asset.asset_code}</strong>
              <span>
                {asset.model_name} · {asset.branch_name}
              </span>
            </div>
            <label>
              Статус<span className="sr-only"> прибора {asset.asset_code}</span>
              <select
                value={asset.state}
                disabled={busy}
                onChange={(event) => void mutate("assets", { state: event.target.value }, asset.id)}
              >
                <option value="available">Доступен</option>
                <option value="rented">В аренде</option>
                <option value="maintenance">Обслуживание</option>
                <option value="inspection">Проверка</option>
                <option value="retired">Списан</option>
              </select>
            </label>
          </article>
        ))}
      </div>
    </section>
  );
}

function PricingSection({ catalog, busy, mutate }: SectionProps) {
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const saved = await mutate("pricing", {
      equipment_model_id: data.get("equipment_model_id"),
      amount_minor_per_day: Math.round(Number(data.get("rubles")) * 100),
      deposit_minor: 0,
      minimum_days: Number(data.get("minimum_days")),
      maximum_days: Number(data.get("maximum_days")),
      state: "draft",
    });
    if (saved) form.reset();
  }
  return (
    <section className="admin-section" aria-labelledby="pricing-title">
      <div className="admin-section-heading">
        <div>
          <p className="eyebrow">Условия</p>
          <h2 id="pricing-title">Тарифы</h2>
        </div>
        <details className="admin-create">
          <summary className="button primary">Добавить тариф</summary>
          <form onSubmit={(event) => void submit(event)}>
            <label>
              Модель
              <select name="equipment_model_id" required>
                {catalog.models.map((model) => (
                  <option key={model.id} value={model.id}>
                    {model.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              ₽ в сутки
              <input name="rubles" required type="number" min="1" max="100000" />
            </label>
            <div className="form-row">
              <label>
                От дней
                <input name="minimum_days" type="number" min="1" max="365" defaultValue="3" />
              </label>
              <label>
                До дней
                <input name="maximum_days" type="number" min="1" max="365" defaultValue="30" />
              </label>
            </div>
            <button className="button primary" disabled={busy || !catalog.models.length}>
              Сохранить черновик
            </button>
          </form>
        </details>
      </div>
      <div className="admin-list">
        {catalog.pricing.map((price) => (
          <article key={price.id}>
            <div>
              <strong>{price.model_name}</strong>
              <span>
                {rubles(price.amount_minor_per_day)} / сутки · {price.minimum_days}–
                {price.maximum_days} дней
              </span>
            </div>
            <label>
              Статус<span className="sr-only"> тарифа {price.model_name}</span>
              <select
                value={price.state}
                disabled={busy}
                onChange={(event) =>
                  void mutate("pricing", { state: event.target.value }, price.id)
                }
              >
                <option value="draft">Черновик</option>
                <option value="active">Активен</option>
                <option value="retired">Архив</option>
              </select>
            </label>
          </article>
        ))}
      </div>
    </section>
  );
}

function BranchSection({ catalog, busy, mutate }: SectionProps) {
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const saved = await mutate("branches", {
      name: data.get("name"),
      city: data.get("city"),
      timezone: data.get("timezone"),
      pickup_enabled: true,
      delivery_enabled: true,
    });
    if (saved) form.reset();
  }
  return (
    <section className="admin-section" aria-labelledby="branches-title">
      <div className="admin-section-heading">
        <div>
          <p className="eyebrow">Получение</p>
          <h2 id="branches-title">Филиалы</h2>
        </div>
        <details className="admin-create">
          <summary className="button primary">Добавить филиал</summary>
          <form onSubmit={(event) => void submit(event)}>
            <label>
              Название
              <input name="name" required maxLength={100} />
            </label>
            <label>
              Город
              <input name="city" required maxLength={100} />
            </label>
            <label>
              Часовой пояс
              <input name="timezone" required defaultValue="Europe/Moscow" />
            </label>
            <button className="button primary" disabled={busy}>
              Добавить
            </button>
          </form>
        </details>
      </div>
      <div className="admin-card-grid">
        {catalog.branches.map((branch) => (
          <article className="admin-card" key={branch.id}>
            <span className={`status status-${branch.state}`}>
              {branch.state === "active" ? "Работает" : "Отключён"}
            </span>
            <h3>{branch.name}</h3>
            <p>
              {branch.city} · {branch.timezone}
            </p>
            <p className="small">
              Самовывоз: {branch.pickup_enabled ? "да" : "нет"} · доставка:{" "}
              {branch.delivery_enabled ? "да" : "нет"}
            </p>
            <button
              className="text-button"
              disabled={busy}
              onClick={() =>
                void mutate(
                  "branches",
                  { state: branch.state === "active" ? "inactive" : "active" },
                  branch.id,
                )
              }
            >
              {branch.state === "active" ? "Отключить" : "Включить"}
            </button>
          </article>
        ))}
      </div>
    </section>
  );
}

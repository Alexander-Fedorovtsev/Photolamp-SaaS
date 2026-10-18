"use client";

import Link from "next/link";
import { CatalogDialog } from "@/components/catalog-dialog";
import { displayError, uiError } from "@/lib/ui-error";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { AdminOrders } from "@/components/admin-orders";
import type { OrderFilter } from "@/components/admin-orders";
import { AdminDashboard } from "@/components/admin-dashboard";
import { AdminTeam } from "@/components/admin-team";
import { AdminSessions } from "@/components/admin-sessions";
import { CourierWorkspace } from "@/components/courier-workspace";
import {
  adminCatalogSchema,
  membershipSchema,
  type AdminCatalog,
  type Membership,
} from "@/lib/admin";

type SessionState = "loading" | "anonymous" | "authenticated" | "error";
type StaffRole = Membership["role"];

const roleLabels: Record<StaffRole, string> = {
  owner: "Владелец",
  admin: "Администратор",
  operator: "Оператор",
  courier: "Курьер",
};

const workspaceAccess: Record<
  StaffRole,
  {
    orders: boolean;
    catalog: boolean;
    team: boolean;
    description: string;
  }
> = {
  owner: {
    orders: true,
    catalog: true,
    team: true,
    description: "Доступны операции, каталог, экземпляры и управление командой.",
  },
  admin: {
    orders: true,
    catalog: true,
    team: true,
    description: "Доступны операции, каталог, экземпляры и просмотр команды.",
  },
  operator: {
    orders: true,
    catalog: false,
    team: false,
    description: "Доступны заказы и операционная сводка без управления парком и командой.",
  },
  courier: {
    orders: false,
    catalog: false,
    team: false,
    description: "Доступны только назначенные доставки и собственные активные сессии.",
  },
};

class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

function sameMemberships(current: Membership[], incoming: Membership[]) {
  return (
    current.length === incoming.length &&
    current.every((membership, index) => {
      const next = incoming[index];
      return (
        next !== undefined &&
        membership.tenant_id === next.tenant_id &&
        membership.slug === next.slug &&
        membership.display_name === next.display_name &&
        membership.timezone === next.timezone &&
        membership.role === next.role
      );
    })
  );
}

async function apiRequest(path: string, init?: RequestInit) {
  const response = await fetch(path, {
    ...init,
    signal: init?.signal ?? AbortSignal.timeout(10000),
    headers: init?.body ? { "Content-Type": "application/json", ...init.headers } : init?.headers,
  });
  const payload = (await response.json().catch(() => ({}))) as { detail?: string };
  if (!response.ok) {
    throw new ApiError(uiError(payload), response.status);
  }
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
  const generation = useRef(0);
  const sessionRevision = useRef(0);
  const [session, setSession] = useState<SessionState>("loading");
  const [memberships, setMemberships] = useState<Membership[]>([]);
  const [tenantId, setTenantId] = useState("");
  const [catalog, setCatalog] = useState<AdminCatalog | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [dashboardRefresh, setDashboardRefresh] = useState(0);
  const [orderTarget, setOrderTarget] = useState<{ filter: OrderFilter; version: number }>({
    filter: "all",
    version: 0,
  });
  const currentTenant = memberships.find((tenant) => tenant.tenant_id === tenantId);
  const access = currentTenant ? workspaceAccess[currentTenant.role] : null;

  const clearSession = useCallback(() => {
    generation.current += 1;
    sessionRevision.current += 1;
    setCatalog(null);
    setMemberships([]);
    setTenantId("");
    setSession("anonymous");
  }, []);

  const clearSharedSession = useCallback(() => {
    clearSession();
    const channel = new BroadcastChannel("photolamp-staff-session");
    channel.postMessage("changed");
    channel.close();
  }, [clearSession]);

  const openOrderQueue = useCallback((filter: OrderFilter) => {
    setOrderTarget((current) => ({ filter, version: current.version + 1 }));
    window.requestAnimationFrame(() => {
      document.getElementById("orders-title")?.scrollIntoView({ behavior: "smooth" });
    });
  }, []);

  const loadSession = useCallback(async () => {
    const attempt = ++sessionRevision.current;
    try {
      const response = await fetch("/api/admin/session", {
        cache: "no-store",
        signal: AbortSignal.timeout(10000),
      });
      if (attempt !== sessionRevision.current) return;
      if (response.status === 401) {
        clearSession();
        return;
      }
      if (!response.ok) throw new Error("Не удалось проверить сессию");
      const data = membershipSchema.array().parse(await response.json());
      if (attempt !== sessionRevision.current) return;
      setMemberships((current) => (sameMemberships(current, data) ? current : data));
      setTenantId((current) =>
        data.some((membership) => membership.tenant_id === current)
          ? current
          : (data.find(
              (membership) =>
                membership.slug ===
                (new URLSearchParams(window.location.search).get("tenant") ??
                  sessionStorage.getItem("photolamp-tenant")),
            )?.tenant_id ??
            data[0]?.tenant_id ??
            ""),
      );
      setSession("authenticated");
    } catch {
      if (attempt !== sessionRevision.current) return;
      setCatalog(null);
      setMemberships([]);
      setNotice("Не удалось проверить доступ. Проверьте соединение и войдите повторно.");
      setSession("error");
    }
  }, [clearSession]);

  const refreshAccess = useCallback(() => {
    setCatalog(null);
    setNotice("Права доступа изменились. Рабочее пространство обновлено.");
    void loadSession();
  }, [loadSession]);

  const loadCatalog = useCallback(
    async (selectedTenant: string, role: StaffRole) => {
      if (!selectedTenant) return;
      if (role === "courier") {
        setCatalog(null);
        return;
      }
      const requestGeneration = generation.current;
      const response = await fetch(
        `/api/admin/catalog?tenant_id=${encodeURIComponent(selectedTenant)}`,
        {
          cache: "no-store",
          signal: AbortSignal.timeout(10000),
        },
      );
      if (response.status === 401) {
        clearSession();
        return;
      }
      if (!response.ok) {
        setCatalog(null);
        if (response.status === 403 || response.status === 404) await loadSession();
        throw new Error("Не удалось загрузить доступное рабочее пространство");
      }
      const nextCatalog = adminCatalogSchema.parse(await response.json());
      if (requestGeneration !== generation.current) return;
      setMemberships((current) => {
        const selected = current.find((membership) => membership.tenant_id === selectedTenant);
        if (!selected || selected.role === nextCatalog.role) return current;
        return current.map((membership) =>
          membership.tenant_id === selectedTenant
            ? { ...membership, role: nextCatalog.role }
            : membership,
        );
      });
      setCatalog(nextCatalog);
    },
    [clearSession, loadSession],
  );

  const refreshDashboard = useCallback(() => {
    setDashboardRefresh((current) => current + 1);
    if (currentTenant)
      void loadCatalog(tenantId, currentTenant.role).catch(() =>
        setNotice("Не удалось обновить инвентарь. Нажмите «Обновить» или перезагрузите страницу."),
      );
  }, [tenantId, currentTenant, loadCatalog]);

  useEffect(() => {
    // Session state is intentionally bootstrapped from the same-origin server boundary.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadSession();
  }, [loadSession]);

  useEffect(() => {
    if (session === "authenticated" && tenantId && currentTenant) {
      // Tenant selection is the external input for the protected catalog fetch.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      void loadCatalog(tenantId, currentTenant.role).catch((error: unknown) => {
        setNotice(displayError(error, "Ошибка загрузки"));
      });
    }
  }, [currentTenant, loadCatalog, session, tenantId]);

  useEffect(() => {
    if (currentTenant) sessionStorage.setItem("photolamp-tenant", currentTenant.slug);
  }, [currentTenant]);

  useEffect(() => {
    // A cookie change is not observable; broadcast every successful login/logout in other tabs.
    const channel = new BroadcastChannel("photolamp-staff-session");
    function invalidate() {
      clearSession();
      setNotice("");
      void loadSession();
    }
    channel.onmessage = invalidate;
    const focus = () => {
      clearSession();
      void loadSession();
    };
    window.addEventListener("focus", focus);
    const visible = () => {
      if (document.visibilityState === "visible") focus();
    };
    document.addEventListener("visibilitychange", visible);
    return () => {
      channel.close();
      window.removeEventListener("focus", focus);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [clearSession, loadSession]);

  function announceSession() {
    const channel = new BroadcastChannel("photolamp-staff-session");
    channel.postMessage("changed");
    channel.close();
  }

  async function demoLogin() {
    setBusy(true);
    setNotice("");
    try {
      await apiRequest("/api/admin/session", { method: "POST" });
      announceSession();
      await loadSession();
    } catch (error) {
      setNotice(displayError(error, "Демо-вход недоступен"));
    } finally {
      setBusy(false);
    }
  }

  async function staffLogin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setBusy(true);
    setNotice("");
    try {
      await apiRequest("/api/admin/session/login", {
        method: "POST",
        body: JSON.stringify({
          login_name: data.get("login_name"),
          password: data.get("password"),
        }),
      });
      announceSession();
      await loadSession();
    } catch (error) {
      setNotice(displayError(error, "Не удалось войти"));
    } finally {
      setBusy(false);
    }
  }

  async function logout() {
    setBusy(true);
    try {
      await apiRequest("/api/admin/session", { method: "DELETE" });
      clearSession();
      announceSession();
    } catch {
      setNotice("Не удалось завершить сессию. Проверьте соединение и повторите выход.");
    } finally {
      setBusy(false);
    }
  }

  async function mutate(resource: string, body: object, id?: string) {
    if (!access?.catalog || !currentTenant) {
      setNotice("Изменение каталога доступно только владельцу или администратору.");
      return false;
    }
    setBusy(true);
    setNotice("");
    try {
      const suffix = id ? `/${encodeURIComponent(id)}` : "";
      await apiRequest(
        `/api/admin/catalog/${resource}${suffix}?tenant_id=${encodeURIComponent(tenantId)}`,
        { method: id ? "PATCH" : "POST", body: JSON.stringify(body) },
      );
      await loadCatalog(tenantId, currentTenant.role);
      setNotice("Изменение сохранено в каталоге организации.");
      return true;
    } catch (error) {
      if (error instanceof ApiError && error.status === 403) {
        setCatalog(null);
        await loadSession();
      }
      setNotice(displayError(error, "Не удалось сохранить изменение"));
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
          <h1>Вход в рабочее пространство.</h1>
          <p className="lead">
            Сотрудники, принявшие приглашение владельца, входят по выбранному логину и паролю.
          </p>
          <form className="admin-auth-form" onSubmit={(event) => void staffLogin(event)}>
            <label>
              Логин
              <input
                name="login_name"
                required
                minLength={4}
                maxLength={32}
                autoCapitalize="none"
                autoCorrect="off"
                autoComplete="username"
              />
            </label>
            <label>
              Пароль
              <input
                name="password"
                type="password"
                required
                minLength={12}
                maxLength={128}
                autoComplete="current-password"
              />
            </label>
            <button className="button primary" type="submit" disabled={busy}>
              {busy ? "Проверяем…" : "Войти"}
            </button>
          </form>
          <div className="admin-login-divider" role="separator">
            <span>или</span>
          </div>
          <button
            className="button secondary"
            type="button"
            onClick={() => void demoLogin()}
            disabled={busy}
          >
            {busy ? "Открываем…" : "Войти в demo-панель"}
          </button>
          <p className="admin-security-note">
            Пароли хранятся только как scrypt-хеши. SMS, восстановление доступа и MFA пока не
            подключены. Demo-вход использует синтетические данные и отключается в production.
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

  return (
    <div className="admin-shell">
      <header className="admin-header">
        <Link href="/" className="brand">
          <span className="brand-mark" aria-hidden="true">
            ☀
          </span>
          <span>Photolamp</span>
        </Link>
        {memberships.length > 0 && (
          <div className="admin-tenant-picker">
            <label htmlFor="tenant-select">Организация</label>
            <select
              id="tenant-select"
              value={tenantId}
              onChange={(event) => {
                generation.current += 1;
                setCatalog(null);
                setNotice("");
                const selected = memberships.find(
                  (membership) => membership.tenant_id === event.target.value,
                );
                if (selected)
                  window.history.replaceState(
                    null,
                    "",
                    `/admin?tenant=${encodeURIComponent(selected.slug)}`,
                  );
                setOrderTarget({ filter: "all", version: 0 });
                setTenantId(event.target.value);
              }}
            >
              {memberships.map((tenant) => (
                <option key={tenant.tenant_id} value={tenant.tenant_id}>
                  {tenant.display_name}
                </option>
              ))}
            </select>
          </div>
        )}
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
              Роль: {currentTenant ? roleLabels[currentTenant.role] : "—"}. {access?.description}
            </p>
          </div>
          {currentTenant && (
            <Link href={`/r/${currentTenant.slug}`} className="button secondary">
              Открыть витрину
            </Link>
          )}
        </section>

        {notice && (
          <p className="admin-alert" role="status" aria-live="polite">
            {notice}
          </p>
        )}
        {access?.orders && (!catalog || catalog.tenant_id !== tenantId) ? (
          <p className="admin-loading">Загружаем каталог организации…</p>
        ) : access?.orders && catalog ? (
          <>
            <AdminDashboard
              tenantId={tenantId}
              refreshKey={dashboardRefresh}
              onUnauthorized={clearSession}
              onAccessChanged={refreshAccess}
              onOpenOrders={openOrderQueue}
            />

            <AdminOrders
              key={`${tenantId}:${orderTarget.version}`}
              tenantId={tenantId}
              timeZone={currentTenant?.timezone ?? "UTC"}
              catalog={catalog}
              onUnauthorized={clearSession}
              onAccessChanged={refreshAccess}
              initialFilter={orderTarget.filter}
              onChanged={refreshDashboard}
            />

            {access.catalog && (
              <>
                <ModelSection
                  notice={notice}
                  key={tenantId}
                  catalog={catalog}
                  busy={busy}
                  mutate={mutate}
                />
                <AssetSection
                  notice={notice}
                  key={tenantId}
                  catalog={catalog}
                  busy={busy}
                  mutate={mutate}
                />
                <PricingSection
                  notice={notice}
                  key={tenantId}
                  catalog={catalog}
                  busy={busy}
                  mutate={mutate}
                />
                <BranchSection
                  notice={notice}
                  key={tenantId}
                  catalog={catalog}
                  busy={busy}
                  mutate={mutate}
                />
              </>
            )}
          </>
        ) : currentTenant?.role === "courier" ? (
          <CourierWorkspace
            key={tenantId}
            tenantId={tenantId}
            onUnauthorized={clearSession}
            onAccessChanged={refreshAccess}
          />
        ) : (
          <section className="admin-section" aria-labelledby="membership-boundary-title">
            <div className="admin-section-heading">
              <div>
                <p className="eyebrow">Доступ</p>
                <h2 id="membership-boundary-title">Нет активной организации</h2>
              </div>
            </div>
            <p>Сессия действует, но активных memberships для рабочего пространства нет.</p>
          </section>
        )}

        {access?.team && (
          <AdminTeam key={tenantId} tenantId={tenantId} onUnauthorized={clearSession} />
        )}

        <AdminSessions onUnauthorized={clearSharedSession} />
      </main>
    </div>
  );
}

type SectionProps = {
  catalog: AdminCatalog;
  notice: string;
  busy: boolean;
  mutate: (resource: string, body: object, id?: string) => Promise<boolean>;
};

function ModelSection({ catalog, busy, mutate, notice }: SectionProps) {
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const saved = await mutate("models", {
      name: data.get("name"),
      manufacturer: String(data.get("manufacturer") ?? "").trim() || null,
      description: data.get("description"),
      included_items: String(data.get("included_items"))
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean),
      illustration: data.get("illustration"),
      publication_state: "draft",
    });
    if (saved) {
      form.reset();
      form.closest("dialog")?.close();
    }
  }
  return (
    <section className="admin-section" aria-labelledby="models-title">
      <div className="admin-section-heading">
        <div>
          <p className="eyebrow">Каталог</p>
          <h2 id="models-title">Модели оборудования</h2>
        </div>
        <CatalogDialog feedback={notice} title="Добавить модель">
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
              Производитель (необязательно)
              <input name="manufacturer" maxLength={120} />
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
        </CatalogDialog>
      </div>
      <div className="admin-card-grid">
        {catalog.models.map((model) => {
          const hasActivePrice = catalog.pricing.some(
            (price) => price.equipment_model_id === model.id && price.state === "active",
          );
          const canPublish = model.publication_state === "published" || hasActivePrice;
          return (
            <article className="admin-card" key={model.id}>
              <span className={`status status-${model.publication_state}`}>
                {model.publication_state === "published"
                  ? "Опубликована"
                  : model.publication_state === "draft"
                    ? "Черновик"
                    : "Архив"}
              </span>
              <h3>{model.name}</h3>
              <EditCatalogItem
                notice={notice}
                resource="models"
                item={model}
                catalog={catalog}
                busy={busy}
                mutate={mutate}
              />
              {model.manufacturer && <p className="small">Производитель: {model.manufacturer}</p>}
              <p>{model.description}</p>
              <p className="small">Комплект: {model.included_items.join(", ")}</p>
              {!hasActivePrice && model.publication_state !== "published" && (
                <p className="small">Для публикации сначала создайте и активируйте тариф.</p>
              )}
              <button
                className="text-button"
                disabled={busy || !canPublish}
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
          );
        })}
      </div>
    </section>
  );
}

function AssetSection({ catalog, busy, mutate, notice }: SectionProps) {
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
    if (saved) {
      form.reset();
      form.closest("dialog")?.close();
    }
  }
  return (
    <section className="admin-section" aria-labelledby="assets-title">
      <div className="admin-section-heading">
        <div>
          <p className="eyebrow">Инвентарь</p>
          <h2 id="assets-title">Экземпляры</h2>
        </div>
        <CatalogDialog feedback={notice} title="Добавить прибор">
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
        </CatalogDialog>
      </div>
      <div className="admin-list">
        {catalog.assets.map((asset) => (
          <article key={asset.id}>
            <div>
              <strong>{asset.asset_code}</strong>
              <EditCatalogItem
                notice={notice}
                resource="assets"
                item={asset}
                catalog={catalog}
                busy={busy}
                mutate={mutate}
              />
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

function PricingSection({ catalog, busy, mutate, notice }: SectionProps) {
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
    if (saved) {
      form.reset();
      form.closest("dialog")?.close();
    }
  }
  return (
    <section className="admin-section" aria-labelledby="pricing-title">
      <div className="admin-section-heading">
        <div>
          <p className="eyebrow">Условия</p>
          <h2 id="pricing-title">Тарифы</h2>
        </div>
        <CatalogDialog feedback={notice} title="Добавить тариф">
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
        </CatalogDialog>
      </div>
      <div className="admin-list">
        {catalog.pricing.map((price) => (
          <article key={price.id}>
            <div>
              <strong>{price.model_name}</strong>
              <EditCatalogItem
                notice={notice}
                resource="pricing"
                item={price}
                catalog={catalog}
                busy={busy}
                mutate={mutate}
              />
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

function BranchSection({ catalog, busy, mutate, notice }: SectionProps) {
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
    if (saved) {
      form.reset();
      form.closest("dialog")?.close();
    }
  }
  return (
    <section className="admin-section" aria-labelledby="branches-title">
      <div className="admin-section-heading">
        <div>
          <p className="eyebrow">Получение</p>
          <h2 id="branches-title">Филиалы</h2>
        </div>
        <CatalogDialog feedback={notice} title="Добавить филиал">
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
        </CatalogDialog>
      </div>
      <div className="admin-card-grid">
        {catalog.branches.map((branch) => (
          <article className="admin-card" key={branch.id}>
            <span className={`status status-${branch.state}`}>
              {branch.state === "active" ? "Работает" : "Отключён"}
            </span>
            <h3>{branch.name}</h3>
            <EditCatalogItem
              notice={notice}
              resource="branches"
              item={branch}
              catalog={catalog}
              busy={busy}
              mutate={mutate}
            />
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

function EditCatalogItem({
  resource,
  notice,
  item,
  catalog,
  busy,
  mutate,
}: SectionProps & {
  resource: "models" | "assets" | "pricing" | "branches";
  item:
    | AdminCatalog["models"][number]
    | AdminCatalog["assets"][number]
    | AdminCatalog["pricing"][number]
    | AdminCatalog["branches"][number];
}) {
  const record = item as unknown as Record<string, unknown>;
  const text = (key: string) => String(record[key] ?? "");
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    const body: Record<string, unknown> = {};
    for (const [key, value] of data) {
      if (key === "included_items")
        body[key] = String(value)
          .split(",")
          .map((part) => part.trim())
          .filter(Boolean);
      else if (["amount_minor_per_day", "deposit_minor"].includes(key))
        body[key] = Math.round(Number(value) * 100);
      else if (["minimum_days", "maximum_days"].includes(key)) body[key] = Number(value);
      else if (["pickup_enabled", "delivery_enabled"].includes(key)) body[key] = value === "true";
      else body[key] = value;
    }
    if (await mutate(resource, body, item.id)) form.closest("dialog")?.close();
  }
  return (
    <CatalogDialog
      feedback={notice}
      title={`Редактировать ${text("name") || text("asset_code") || text("model_name")}`}
    >
      <form onSubmit={(event) => void save(event)}>
        {resource === "models" && (
          <>
            <label>
              Название
              <input name="name" required maxLength={120} defaultValue={text("name")} />
            </label>
            <label>
              Описание
              <textarea
                name="description"
                required
                maxLength={500}
                defaultValue={text("description")}
              />
            </label>
            <label>
              Комплект через запятую
              <input
                name="included_items"
                required
                defaultValue={(record.included_items as string[]).join(", ")}
              />
            </label>
          </>
        )}
        {resource === "assets" && (
          <>
            <label>
              Внутренний код
              <input
                name="asset_code"
                required
                pattern="[A-Z0-9][A-Z0-9-]{1,31}"
                defaultValue={text("asset_code")}
              />
            </label>
            <label>
              Модель
              <select name="equipment_model_id" defaultValue={text("equipment_model_id")}>
                {catalog.models.map((model) => (
                  <option key={model.id} value={model.id}>
                    {model.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Филиал
              <select name="branch_id" defaultValue={text("branch_id")}>
                {catalog.branches.map((branch) => (
                  <option key={branch.id} value={branch.id}>
                    {branch.name}
                  </option>
                ))}
              </select>
            </label>
            <p className="small">
              Прибор с активным заказом изменяется через операции заказа. Сервер повторно проверяет
              это перед сохранением.
            </p>
          </>
        )}
        {resource === "pricing" && (
          <>
            <label>
              ₽ в сутки
              <input
                name="amount_minor_per_day"
                type="number"
                min="0.01"
                step="0.01"
                max="100000"
                required
                defaultValue={Number(record.amount_minor_per_day) / 100}
              />
            </label>
            <label>
              Залог, ₽
              <input
                name="deposit_minor"
                type="number"
                min="0"
                step="0.01"
                max="1000000"
                required
                defaultValue={Number(record.deposit_minor) / 100}
              />
            </label>
            <label>
              От дней
              <input
                name="minimum_days"
                type="number"
                min="1"
                max="365"
                required
                defaultValue={text("minimum_days")}
              />
            </label>
            <label>
              До дней
              <input
                name="maximum_days"
                type="number"
                min="1"
                max="365"
                required
                defaultValue={text("maximum_days")}
              />
            </label>
            <p className="small">
              Изменения применятся к новым расчётам. Стоимость оформленных заказов сохранится.
            </p>
          </>
        )}
        {resource === "branches" && (
          <>
            <label>
              Название
              <input name="name" required maxLength={100} defaultValue={text("name")} />
            </label>
            <label>
              Город
              <input name="city" required maxLength={100} defaultValue={text("city")} />
            </label>
            <label>
              Часовой пояс
              <input name="timezone" required maxLength={100} defaultValue={text("timezone")} />
            </label>
            <label>
              Самовывоз
              <select name="pickup_enabled" defaultValue={text("pickup_enabled")}>
                <option value="true">Доступен</option>
                <option value="false">Выключен</option>
              </select>
            </label>
            <label>
              Доставка
              <select name="delivery_enabled" defaultValue={text("delivery_enabled")}>
                <option value="true">Доступна</option>
                <option value="false">Выключена</option>
              </select>
            </label>
          </>
        )}
        <button className="button primary" type="submit" disabled={busy}>
          {busy ? "Сохраняем…" : "Сохранить изменения"}
        </button>
      </form>
    </CatalogDialog>
  );
}

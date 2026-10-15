import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import type { AdminOrder, StaffMember } from "../src/lib/admin";

const orderFixture = {
  order_id: "d9000000-0000-0000-0000-000000000001",
  state: "held",
  source: "website",
  snapshot: {
    schema_version: 1,
    model_id: "d2000000-0000-0000-0000-000000000001",
    model_name: "Синтетическая фотолампа",
    branch_id: "d1000000-0000-0000-0000-000000000001",
    branch_name: "Демо-точка",
    start_date: "2030-01-10",
    end_date: "2030-01-12",
    fulfillment: "pickup",
    quote: {
      model_id: "d2000000-0000-0000-0000-000000000001",
      branch_id: "d1000000-0000-0000-0000-000000000001",
      start_date: "2030-01-10",
      end_date: "2030-01-12",
      days: 3,
      available_units: 1,
      available: true,
      availability_confirmed: true,
      reservation_created: false,
      fulfillment: "pickup",
      amount_minor_per_day: 49_000,
      rental_amount_minor: 147_000,
      deposit_minor: 0,
      delivery_amount_minor: 0,
      total_minor: 147_000,
      currency: "RUB",
      price_is_demo: true,
      booking_enabled: false,
    },
  },
  planned_end_date: "2030-01-12",
  is_overdue: false,
  overdue_days: 0,
  extension_request: null,
  return_request: null,
  identity_verified: true,
  payment_status: "not_started",
  expires_at: "2030-01-01T12:15:00+00:00",
  confirmed_at: null,
  handed_over_at: null,
  returned_at: null,
  completed_at: null,
  inspection_outcome: null,
  created_at: "2030-01-01T12:00:00+00:00",
  updated_at: "2030-01-01T12:00:00+00:00",
} as const satisfies AdminOrder;

const phoneOrderFixture = {
  ...orderFixture,
  state: "confirmed",
  source: "phone",
  identity_verified: false,
  expires_at: null,
  confirmed_at: "2030-01-01T12:05:00+00:00",
  updated_at: "2030-01-01T12:05:00+00:00",
} as const satisfies AdminOrder;

const dashboardFixture = {
  tenant_id: "d0000000-0000-0000-0000-000000000001",
  timezone: "Asia/Yekaterinburg",
  local_date: "2030-01-10",
  generated_at: "2030-01-10T06:00:00+00:00",
  counts: {
    awaiting_confirmation: 1,
    handovers_due: 1,
    returns_today: 0,
    returns_tomorrow: 0,
    overdue: 0,
    inspection: 0,
    pending_requests: 1,
  },
  inventory: { available: 2, rented: 1, inspection: 0, maintenance: 0, retired: 0 },
  handovers_due: [],
  returns_today: [],
  returns_tomorrow: [],
  overdue: [],
  pending_requests: [],
};

test.beforeEach(async ({ page }) => {
  await page.route(/\/api\/admin\/courier\/dispatch\?.*$/, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: '{"couriers":[],"assignments":[]}',
    });
  });
});

test("dashboard opens the customer request queue with one action", async ({ page }) => {
  let requestFilterSeen = false;
  await page.route(/\/api\/admin\/dashboard\?.*$/, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(dashboardFixture),
    });
  });
  await page.route(/\/api\/admin\/orders\?.*$/, async (route) => {
    const url = new URL(route.request().url());
    if (url.searchParams.get("requests") === "true") requestFilterSeen = true;
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ items: [orderFixture], total: 1, limit: 100, offset: 0 }),
    });
  });

  await page.goto("/admin");
  await page.getByRole("button", { name: "Войти в demo-панель" }).click();
  await expect(page.getByRole("heading", { name: "Операционная сводка" })).toBeVisible();
  await expect(page.getByLabel("Состояние парка")).toContainText("В аренде1");
  await page.getByRole("button", { name: /1 Запросы клиентов/ }).click();
  await expect.poll(() => requestFilterSeen).toBe(true);
  await expect(page.getByLabel("Статус", { exact: true })).toHaveValue("requests");
});

test("owner can update a synthetic staff role without exposing contacts", async ({ page }) => {
  const owner = {
    user_id: "d5000000-0000-0000-0000-000000000001",
    display_name: "Демо-владелец",
    role: "owner",
    state: "active",
    is_current_user: true,
    created_at: "2030-01-01T12:00:00+00:00",
    updated_at: "2030-01-01T12:00:00+00:00",
  } as const;
  let operator: StaffMember = {
    user_id: "d5000000-0000-0000-0000-000000000002",
    display_name: "Демо-оператор",
    role: "operator",
    state: "active",
    is_current_user: false,
    created_at: "2030-01-01T12:00:00+00:00",
    updated_at: "2030-01-01T12:00:00+00:00",
  };
  await page.route(/\/api\/admin\/team\?.*$/, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        tenant_id: dashboardFixture.tenant_id,
        can_manage: true,
        members: [owner, operator],
      }),
    });
  });
  await page.route(/\/api\/admin\/team\/invitations\?.*$/, async (route) => {
    await route.fulfill({ contentType: "application/json", body: "[]" });
  });
  await page.route(/\/api\/admin\/team\/[0-9a-f-]+\?.*$/, async (route) => {
    const body = route.request().postDataJSON() as {
      display_name: string;
      role: "owner" | "admin" | "operator" | "courier";
      state: "active" | "disabled";
    };
    expect(body).toEqual({
      display_name: "Демо-координатор",
      role: "admin",
      state: "disabled",
    });
    expect(JSON.stringify(body)).not.toContain("phone");
    operator = {
      ...operator,
      ...body,
      updated_at: "2030-01-01T12:05:00+00:00",
    };
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(operator) });
  });
  page.on("dialog", (dialog) => void dialog.accept());

  await page.goto("/admin");
  await page.getByRole("button", { name: "Войти в demo-панель" }).click();
  await expect(page.getByRole("heading", { name: "Команда и роли" })).toBeVisible();
  const card = page.getByRole("article").filter({ hasText: "Демо-оператор" });
  await card.getByLabel("Отображаемое имя").fill("Демо-координатор");
  await card.getByLabel("Роль").selectOption("admin");
  await card.getByLabel("Доступ").selectOption("disabled");
  await card.getByRole("button", { name: "Сохранить права" }).click();
  await expect(page.getByText("Изменения доступа сохранены и записаны в аудит.")).toBeVisible();
  await expect(page.getByText("Демо-координатор", { exact: true })).toBeVisible();
  await expect(page.getByText("Доступ отключён", { exact: true })).toBeVisible();
});

test("demo staff session opens a tenant-isolated catalog workspace", async ({ page }) => {
  await page.goto("/admin");
  await expect(page.getByRole("heading", { name: "Вход в рабочее пространство." })).toBeVisible();
  await page.getByRole("button", { name: "Войти в demo-панель" }).click();
  await expect(page.getByRole("heading", { name: "Бережный свет · демо" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Операционная сводка" })).toBeVisible();
  await page.getByLabel("Организация").selectOption({ label: "Солнечный дом · демо" });
  await expect(page.getByRole("heading", { name: "Солнечный дом · демо" })).toBeVisible();
  await expect(page.getByText("DEMO-UFA-01", { exact: true })).toBeVisible();
  await expect(page.getByText("DEMO-KZN-01", { exact: true })).toHaveCount(0);
});

test("owner creates a one-time staff link and invitee sets up access", async ({ page }) => {
  const invitationToken = `pli_${"a".repeat(43)}`;
  const invitedMember: StaffMember = {
    user_id: "d5000000-0000-0000-0000-000000000009",
    display_name: "Новый курьер",
    role: "courier",
    state: "invited",
    is_current_user: false,
    created_at: "2030-01-01T12:00:00+00:00",
    updated_at: "2030-01-01T12:00:00+00:00",
  };
  await page.route(/\/api\/admin\/team\?.*$/, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        tenant_id: dashboardFixture.tenant_id,
        can_manage: true,
        members: [],
      }),
    });
  });
  await page.route(/\/api\/admin\/team\/invitations\?.*$/, async (route) => {
    if (route.request().method() === "POST") {
      expect(route.request().postDataJSON()).toEqual({
        display_name: "Новый курьер",
        role: "courier",
      });
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          invitation_id: "d6000000-0000-0000-0000-000000000009",
          invitation_token: invitationToken,
          expires_at: "2030-01-03T12:00:00+00:00",
          member: invitedMember,
        }),
      });
      return;
    }
    await route.fulfill({ contentType: "application/json", body: "[]" });
  });

  await page.goto("/admin");
  await page.getByRole("button", { name: "Войти в demo-панель" }).click();
  await page.getByRole("button", { name: "Добавить сотрудника" }).click();
  const invitationForm = page.locator(".admin-invitation-form");
  await invitationForm.getByLabel("Имя в команде").fill("Новый курьер");
  await invitationForm.locator('select[name="role"]').selectOption("courier");
  await invitationForm.getByRole("button", { name: "Создать ссылку" }).click();
  const link = page.getByLabel("Одноразовая ссылка приглашения");
  await expect(link).toHaveValue(new RegExp(`/admin/invite#token=${invitationToken}`));
  await expect(
    page.getByText("После обновления страницы она не будет показана повторно."),
  ).toBeVisible();

  await page.route("/api/admin/invitations/redeem", async (route) => {
    expect(route.request().postDataJSON()).toEqual({
      invitation_token: invitationToken,
      login_name: "courier.demo",
      password: "synthetic-password-42",
    });
    await route.fulfill({ contentType: "application/json", body: '{"authenticated":true}' });
  });
  await page.goto(`/admin/invite#token=${invitationToken}`);
  await expect(page).toHaveURL(/\/admin\/invite$/);
  await page.getByLabel("Логин").fill("courier.demo");
  await page.getByLabel("Пароль", { exact: true }).fill("synthetic-password-42");
  await page.getByLabel("Повторите пароль").fill("synthetic-password-42");
  await page.getByRole("button", { name: "Принять приглашение" }).click();
  await page.waitForURL("/admin");
});

test("staff can review sessions, revoke one device and sign out everywhere", async ({ page }) => {
  const currentSession = {
    session_id: "d7000000-0000-0000-0000-000000000001",
    client_label: "Chrome · Linux",
    created_at: "2030-01-01T12:00:00+00:00",
    last_seen_at: "2030-01-01T12:05:00+00:00",
    expires_at: "2030-01-02T00:00:00+00:00",
    is_current: true,
  };
  const otherSession = {
    session_id: "d7000000-0000-0000-0000-000000000002",
    client_label: "Safari · macOS",
    created_at: "2030-01-01T10:00:00+00:00",
    last_seen_at: "2030-01-01T11:00:00+00:00",
    expires_at: "2030-01-01T22:00:00+00:00",
    is_current: false,
  };
  let sessions = [currentSession, otherSession];
  await page.route(/\/api\/admin\/sessions$/, async (route) => {
    if (route.request().method() === "DELETE") {
      sessions = [];
      await route.fulfill({ contentType: "application/json", body: '{"revoked_count":1}' });
      return;
    }
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(sessions) });
  });
  await page.route(/\/api\/admin\/sessions\/[0-9a-f-]+$/, async (route) => {
    expect(route.request().method()).toBe("DELETE");
    sessions = [currentSession];
    await route.fulfill({
      contentType: "application/json",
      body: '{"revoked":true,"revoked_current":false}',
    });
  });
  page.on("dialog", (dialog) => void dialog.accept());

  await page.goto("/admin");
  await page.getByRole("button", { name: "Войти в demo-панель" }).click();
  await expect(page.getByRole("heading", { name: "Активные сессии" })).toBeVisible();
  await expect(page.getByText("Chrome · Linux", { exact: true })).toBeVisible();
  const otherCard = page.getByRole("article").filter({ hasText: "Safari · macOS" });
  await otherCard.getByRole("button", { name: "Завершить" }).click();
  await expect(page.getByText("Safari · macOS", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Выйти на всех устройствах" }).click();
  await expect(page.getByRole("heading", { name: "Вход в рабочее пространство." })).toBeVisible();
});

test("courier completes only the assigned delivery from the restricted workspace", async ({
  page,
}) => {
  const protectedRequests: string[] = [];
  let taskState: "assigned" | "accepted" | "picked_up" | "delivered" = "assigned";
  const courierTask = () => ({
    task_id: "da000000-0000-0000-0000-000000000001",
    order_id: phoneOrderFixture.order_id,
    state: taskState,
    snapshot: { ...phoneOrderFixture.snapshot, fulfillment: "delivery" as const },
    planned_end_date: phoneOrderFixture.planned_end_date,
    assigned_at: "2030-01-01T12:10:00+00:00",
    accepted_at: taskState === "assigned" ? null : "2030-01-01T12:11:00+00:00",
    picked_up_at:
      taskState === "picked_up" || taskState === "delivered" ? "2030-01-01T12:20:00+00:00" : null,
    delivered_at: taskState === "delivered" ? "2030-01-01T12:40:00+00:00" : null,
    updated_at: "2030-01-01T12:40:00+00:00",
  });
  page.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (/^\/api\/admin\/(catalog|dashboard|orders|team)(?:\/|$)/.test(path)) {
      protectedRequests.push(path);
    }
  });
  await page.route(/\/api\/admin\/session$/, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify([
        {
          tenant_id: dashboardFixture.tenant_id,
          slug: "demo-ufa",
          display_name: "Бережный свет · демо",
          timezone: "Asia/Yekaterinburg",
          role: "courier",
        },
      ]),
    });
  });
  await page.route(/\/api\/admin\/sessions$/, async (route) => {
    await route.fulfill({ contentType: "application/json", body: "[]" });
  });
  await page.route(/\/api\/admin\/courier\/tasks\?.*$/, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ items: [courierTask()] }),
    });
  });
  await page.route(
    /\/api\/admin\/courier\/tasks\/[^/]+\/(accept|pickup|deliver)\?.*$/,
    async (route) => {
      const action = new URL(route.request().url()).pathname.split("/").at(-1);
      taskState =
        action === "accept" ? "accepted" : action === "pickup" ? "picked_up" : "delivered";
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ task: courierTask(), replayed: false }),
      });
    },
  );
  await page.route(/\/api\/admin\/courier\/tasks\/[^/]+\/contact\?.*$/, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        contact: {
          phone_e164: "+70000000000",
          recipient_name: "Тестовый клиент",
          delivery_address: "Тестовый адрес",
        },
        replayed: false,
      }),
    });
  });
  page.on("dialog", (dialog) => void dialog.accept());

  await page.goto("/admin");

  await expect(page.getByText("Роль: Курьер.")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Мои задания" })).toBeVisible();
  await expect(page.getByText("Синтетическая фотолампа", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Принять задание" }).click();
  await page.getByRole("button", { name: "Показать контакт" }).click();
  await expect(page.getByText("Тестовый адрес", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Лампа получена" }).click();
  await page.getByRole("button", { name: "Доставлено клиенту" }).click();
  await expect(page.getByText("Доставлено", { exact: true })).toBeVisible();
  await expect(page.getByText("Тестовый адрес", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Активные сессии" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Операционная сводка" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Заказы", exact: true })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Экземпляры" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Команда и роли" })).toHaveCount(0);
  await expect.poll(() => protectedRequests).toEqual([]);
});

test("operator assigns a courier to a confirmed delivery", async ({ page }) => {
  const courierUserId = "d5000000-0000-0000-0000-000000000003";
  let assignment: Record<string, unknown> | null = null;
  const deliveryOrder = {
    ...phoneOrderFixture,
    snapshot: { ...phoneOrderFixture.snapshot, fulfillment: "delivery" as const },
  };
  await page.route(/\/api\/admin\/orders\?.*$/, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ items: [deliveryOrder], total: 1, limit: 100, offset: 0 }),
    });
  });
  await page.route(/\/api\/admin\/courier\/dispatch\?.*$/, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        couriers: [{ user_id: courierUserId, display_name: "Демо-курьер" }],
        assignments: assignment ? [assignment] : [],
      }),
    });
  });
  await page.route(/\/api\/admin\/orders\/[^/]+\/courier\?.*$/, async (route) => {
    expect(route.request().method()).toBe("PUT");
    expect(route.request().postDataJSON()).toEqual({ courier_user_id: courierUserId });
    assignment = {
      task_id: "da000000-0000-0000-0000-000000000001",
      order_id: phoneOrderFixture.order_id,
      courier_user_id: courierUserId,
      courier_display_name: "Демо-курьер",
      state: "assigned",
      assigned_at: "2030-01-01T12:10:00+00:00",
      accepted_at: null,
      picked_up_at: null,
      delivered_at: null,
      updated_at: "2030-01-01T12:10:00+00:00",
    };
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ assignment, replayed: false }),
    });
  });

  await page.goto("/admin");
  await page.getByRole("button", { name: "Войти в demo-панель" }).click();
  await page.getByText("Синтетическая фотолампа", { exact: true }).click();
  await page
    .getByLabel(`Курьер для заказа ${phoneOrderFixture.order_id.slice(0, 8)}`)
    .selectOption(courierUserId);

  await expect(page.getByText("Курьер назначен на доставку.")).toBeVisible();
  await expect(page.getByText("Демо-курьер · Назначен", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Передать в доставку" })).toHaveCount(0);
});

test("authenticated staff without an active organization gets a clear access boundary", async ({
  page,
}) => {
  const protectedRequests: string[] = [];
  page.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (/^\/api\/admin\/(catalog|dashboard|orders|team)(?:\/|$)/.test(path)) {
      protectedRequests.push(path);
    }
  });
  await page.route(/\/api\/admin\/session$/, async (route) => {
    await route.fulfill({ contentType: "application/json", body: "[]" });
  });
  await page.route(/\/api\/admin\/sessions$/, async (route) => {
    await route.fulfill({ contentType: "application/json", body: "[]" });
  });

  await page.goto("/admin");

  await expect(page.getByRole("heading", { name: "Нет активной организации" })).toBeVisible();
  await expect(page.getByLabel("Организация")).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Открыть витрину" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Активные сессии" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Мои задания" })).toHaveCount(0);
  await expect.poll(() => protectedRequests).toEqual([]);
});

test("operator gets order tools without private inventory or team controls", async ({ page }) => {
  const requestedPaths: string[] = [];
  page.on("request", (request) => requestedPaths.push(new URL(request.url()).pathname));
  await page.route(/\/api\/admin\/session$/, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify([
        {
          tenant_id: dashboardFixture.tenant_id,
          slug: "demo-ufa",
          display_name: "Бережный свет · демо",
          timezone: "Asia/Yekaterinburg",
          role: "operator",
        },
      ]),
    });
  });
  await page.route(/\/api\/admin\/catalog\?.*$/, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        tenant_id: dashboardFixture.tenant_id,
        role: "operator",
        branches: [
          {
            id: "d1000000-0000-0000-0000-000000000001",
            name: "Демо-точка",
            city: "Уфа",
            timezone: "Asia/Yekaterinburg",
            pickup_enabled: true,
            delivery_enabled: true,
            state: "active",
          },
        ],
        models: [
          {
            id: "d2000000-0000-0000-0000-000000000001",
            name: "Синтетическая фотолампа",
            manufacturer: null,
            description: "Тестовая модель",
            included_items: ["лампа"],
            illustration: "compact",
            publication_state: "published",
          },
        ],
        pricing: [],
        assets: [
          {
            id: "d3000000-0000-0000-0000-000000000001",
            branch_id: "d1000000-0000-0000-0000-000000000001",
            branch_name: "Демо-точка",
            equipment_model_id: "d2000000-0000-0000-0000-000000000001",
            model_name: "Синтетическая фотолампа",
            asset_code: "PRIVATE-ASSET-01",
            serial_no: "PRIVATE-SERIAL-01",
            state: "available",
          },
        ],
        asset_blocks: [],
      }),
    });
  });
  await page.route(/\/api\/admin\/dashboard\?.*$/, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(dashboardFixture),
    });
  });
  await page.route(/\/api\/admin\/orders\?.*$/, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: '{"items":[],"total":0,"limit":100,"offset":0}',
    });
  });
  await page.route(/\/api\/admin\/sessions$/, async (route) => {
    await route.fulfill({ contentType: "application/json", body: "[]" });
  });
  await page.route(/\/api\/admin\/courier\/tasks\?.*$/, async (route) => {
    await route.fulfill({ contentType: "application/json", body: '{"items":[]}' });
  });

  await page.goto("/admin");

  await expect(page.getByText("Роль: Оператор.")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Операционная сводка" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Заказы", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Экземпляры" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Модели оборудования" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Команда и роли" })).toHaveCount(0);
  await expect(page.getByText("PRIVATE-ASSET-01", { exact: true })).toHaveCount(0);
  expect(requestedPaths.some((path) => path === "/api/admin/team")).toBe(false);
});

test("owner can add an inventory instance from the workspace", async ({ page }) => {
  const branchId = "d1000000-0000-0000-0000-000000000001";
  const modelId = "d2000000-0000-0000-0000-000000000001";
  const assets: Array<Record<string, unknown>> = [];
  const catalog = () => ({
    tenant_id: dashboardFixture.tenant_id,
    role: "owner",
    branches: [
      {
        id: branchId,
        name: "Демо-точка",
        city: "Уфа",
        timezone: "Asia/Yekaterinburg",
        pickup_enabled: true,
        delivery_enabled: true,
        state: "active",
      },
    ],
    models: [
      {
        id: modelId,
        name: "Синтетическая фотолампа",
        manufacturer: null,
        description: "Тестовая модель",
        included_items: ["лампа"],
        illustration: "compact",
        publication_state: "published",
      },
    ],
    pricing: [],
    assets,
    asset_blocks: [],
  });
  await page.route(/\/api\/admin\/session$/, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify([
        {
          tenant_id: dashboardFixture.tenant_id,
          slug: "demo-ufa",
          display_name: "Бережный свет · демо",
          timezone: "Asia/Yekaterinburg",
          role: "owner",
        },
      ]),
    });
  });
  await page.route(/\/api\/admin\/catalog(?:\/assets)?\?.*$/, async (route) => {
    if (route.request().method() === "POST") {
      expect(route.request().postDataJSON()).toEqual({
        asset_code: "DEMO-UFA-99",
        branch_id: branchId,
        equipment_model_id: modelId,
        state: "available",
      });
      assets.push({
        id: "d3000000-0000-0000-0000-000000000099",
        branch_id: branchId,
        branch_name: "Демо-точка",
        equipment_model_id: modelId,
        model_name: "Синтетическая фотолампа",
        asset_code: "DEMO-UFA-99",
        serial_no: null,
        state: "available",
      });
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: '{"id":"d3000000-0000-0000-0000-000000000099"}',
      });
      return;
    }
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(catalog()) });
  });
  await page.route(/\/api\/admin\/dashboard\?.*$/, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(dashboardFixture),
    });
  });
  await page.route(/\/api\/admin\/orders\?.*$/, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: '{"items":[],"total":0,"limit":100,"offset":0}',
    });
  });
  await page.route(/\/api\/admin\/team\?.*$/, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        tenant_id: dashboardFixture.tenant_id,
        can_manage: true,
        members: [],
      }),
    });
  });
  await page.route(/\/api\/admin\/team\/invitations\?.*$/, async (route) => {
    await route.fulfill({ contentType: "application/json", body: "[]" });
  });
  await page.route(/\/api\/admin\/sessions$/, async (route) => {
    await route.fulfill({ contentType: "application/json", body: "[]" });
  });

  await page.goto("/admin");
  await page.getByText("Добавить прибор", { exact: true }).click();
  await page.getByLabel("Внутренний код").fill("DEMO-UFA-99");
  await page.getByRole("button", { name: "Добавить", exact: true }).click();

  await expect(page.getByText("DEMO-UFA-99", { exact: true })).toBeVisible();
  await expect(page.getByText("Изменение сохранено в каталоге организации.")).toBeVisible();
});

test("a server-side role downgrade removes stale catalog controls", async ({ page }) => {
  let role: "owner" | "courier" = "owner";
  await page.route(/\/api\/admin\/session$/, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify([
        {
          tenant_id: dashboardFixture.tenant_id,
          slug: "demo-ufa",
          display_name: "Бережный свет · демо",
          timezone: "Asia/Yekaterinburg",
          role,
        },
      ]),
    });
  });
  await page.route(/\/api\/admin\/catalog\?.*$/, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        tenant_id: dashboardFixture.tenant_id,
        role: "owner",
        branches: [],
        models: [
          {
            id: "d2000000-0000-0000-0000-000000000001",
            name: "Синтетическая фотолампа",
            manufacturer: null,
            description: "Тестовая модель",
            included_items: ["лампа"],
            illustration: "compact",
            publication_state: "published",
          },
        ],
        pricing: [],
        assets: [],
        asset_blocks: [],
      }),
    });
  });
  await page.route(/\/api\/admin\/catalog\/models\/[^?]+\?.*$/, async (route) => {
    role = "courier";
    await route.fulfill({
      status: 403,
      contentType: "application/json",
      body: '{"detail":"Owner or admin role required"}',
    });
  });
  await page.route(/\/api\/admin\/dashboard\?.*$/, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(dashboardFixture),
    });
  });
  await page.route(/\/api\/admin\/orders\?.*$/, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: '{"items":[],"total":0,"limit":100,"offset":0}',
    });
  });
  await page.route(/\/api\/admin\/team\?.*$/, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        tenant_id: dashboardFixture.tenant_id,
        can_manage: true,
        members: [],
      }),
    });
  });
  await page.route(/\/api\/admin\/team\/invitations\?.*$/, async (route) => {
    await route.fulfill({ contentType: "application/json", body: "[]" });
  });
  await page.route(/\/api\/admin\/sessions$/, async (route) => {
    await route.fulfill({ contentType: "application/json", body: "[]" });
  });
  await page.route(/\/api\/admin\/courier\/tasks\?.*$/, async (route) => {
    await route.fulfill({ contentType: "application/json", body: '{"items":[]}' });
  });

  await page.goto("/admin");
  await page.getByRole("button", { name: "Снять с витрины" }).click();

  await expect(page.getByText("Роль: Курьер.")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Мои задания" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Модели оборудования" })).toHaveCount(0);
});

test("a role downgrade purges an already revealed order contact", async ({ page }) => {
  let role: "operator" | "courier" = "operator";
  await page.route(/\/api\/admin\/session$/, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify([
        {
          tenant_id: dashboardFixture.tenant_id,
          slug: "demo-ufa",
          display_name: "Бережный свет · демо",
          timezone: "Asia/Yekaterinburg",
          role,
        },
      ]),
    });
  });
  await page.route(/\/api\/admin\/catalog\?.*$/, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        tenant_id: dashboardFixture.tenant_id,
        role: "operator",
        branches: [],
        models: [],
        pricing: [],
        assets: [],
        asset_blocks: [],
      }),
    });
  });
  await page.route(/\/api\/admin\/dashboard\?.*$/, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(dashboardFixture),
    });
  });
  await page.route(/\/api\/admin\/orders\?.*$/, async (route) => {
    if (role === "courier") {
      await route.fulfill({
        status: 403,
        contentType: "application/json",
        body: '{"detail":"Owner, admin or operator role required"}',
      });
      return;
    }
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ items: [phoneOrderFixture], total: 1, limit: 100, offset: 0 }),
    });
  });
  await page.route(/\/api\/admin\/orders\/[^/]+\/contact\?.*$/, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        contact: {
          phone_e164: "+70000000000",
          recipient_name: "Тестовый клиент",
          delivery_address: null,
        },
        replayed: false,
      }),
    });
  });
  await page.route(/\/api\/admin\/sessions$/, async (route) => {
    await route.fulfill({ contentType: "application/json", body: "[]" });
  });
  await page.route(/\/api\/admin\/courier\/tasks\?.*$/, async (route) => {
    await route.fulfill({ contentType: "application/json", body: '{"items":[]}' });
  });

  await page.goto("/admin");
  await page.getByText("Синтетическая фотолампа", { exact: true }).click();
  await page.getByRole("button", { name: "Показать контакт" }).click();
  await expect(page.getByRole("link", { name: "+70000000000" })).toBeVisible();
  role = "courier";
  await page.locator(".admin-orders").getByRole("button", { name: "Обновить" }).click();

  await expect(page.getByText("Роль: Курьер.")).toBeVisible();
  await expect(page.getByRole("link", { name: "+70000000000" })).toHaveCount(0);
  await expect(page.getByText("Тестовый клиент", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Заказы", exact: true })).toHaveCount(0);
});

test("a disabled membership purges the tenant workspace and revealed contact", async ({ page }) => {
  let membershipActive = true;
  await page.route(/\/api\/admin\/session$/, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: membershipActive
        ? JSON.stringify([
            {
              tenant_id: dashboardFixture.tenant_id,
              slug: "demo-ufa",
              display_name: "Бережный свет · демо",
              timezone: "Asia/Yekaterinburg",
              role: "operator",
            },
          ])
        : "[]",
    });
  });
  await page.route(/\/api\/admin\/catalog\?.*$/, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        tenant_id: dashboardFixture.tenant_id,
        role: "operator",
        branches: [],
        models: [],
        pricing: [],
        assets: [],
        asset_blocks: [],
      }),
    });
  });
  await page.route(/\/api\/admin\/dashboard\?.*$/, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(dashboardFixture),
    });
  });
  await page.route(/\/api\/admin\/orders\?.*$/, async (route) => {
    if (!membershipActive) {
      await route.fulfill({
        status: 404,
        contentType: "application/json",
        body: '{"detail":"Tenant not found"}',
      });
      return;
    }
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ items: [phoneOrderFixture], total: 1, limit: 100, offset: 0 }),
    });
  });
  await page.route(/\/api\/admin\/orders\/[^/]+\/contact\?.*$/, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        contact: {
          phone_e164: "+70000000000",
          recipient_name: "Тестовый клиент",
          delivery_address: null,
        },
        replayed: false,
      }),
    });
  });
  await page.route(/\/api\/admin\/sessions$/, async (route) => {
    await route.fulfill({ contentType: "application/json", body: "[]" });
  });

  await page.goto("/admin");
  await page.getByText("Синтетическая фотолампа", { exact: true }).click();
  await page.getByRole("button", { name: "Показать контакт" }).click();
  await expect(page.getByRole("link", { name: "+70000000000" })).toBeVisible();
  membershipActive = false;
  await page.locator(".admin-orders").getByRole("button", { name: "Обновить" }).click();

  await expect(page.getByRole("heading", { name: "Нет активной организации" })).toBeVisible();
  await expect(page.getByLabel("Организация")).toHaveCount(0);
  await expect(page.getByRole("link", { name: "+70000000000" })).toHaveCount(0);
  await expect(page.getByText("Тестовый клиент", { exact: true })).toHaveCount(0);
});

test("admin workspace is keyboard reachable and accessible", async ({ page }) => {
  await page.goto("/admin");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Перейти к содержимому" })).toBeFocused();
  await page.getByRole("button", { name: "Войти в demo-панель" }).click();
  await expect(page.getByRole("heading", { name: /демо$/ })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect(results.violations).toEqual([]);
});

test("staff can review and confirm an order from the tenant queue", async ({ page }) => {
  let currentOrder: AdminOrder = orderFixture;
  await page.route(/\/api\/admin\/orders\?.*$/, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ items: [currentOrder], total: 1, limit: 100, offset: 0 }),
    });
  });
  await page.route(/\/api\/admin\/orders\/[^/]+\/confirm\?.*$/, async (route) => {
    expect(route.request().headers()["idempotency-key"]).toMatch(/^admin-confirm-/);
    currentOrder = {
      ...currentOrder,
      state: "confirmed",
      confirmed_at: "2030-01-01T12:05:00+00:00",
      updated_at: "2030-01-01T12:05:00+00:00",
    };
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ order: currentOrder, replayed: false }),
    });
  });
  page.on("dialog", (dialog) => void dialog.accept());

  await page.goto("/admin");
  await page.getByRole("button", { name: "Войти в demo-панель" }).click();
  await expect(page.getByRole("heading", { name: "Заказы" })).toBeVisible();
  await page.getByText("Синтетическая фотолампа", { exact: true }).click();
  await expect(page.getByText("Телефон подтверждён", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
  await page.getByRole("button", { name: "Подтвердить" }).click();
  await expect(page.getByText("Заказ подтверждён.", { exact: true })).toBeVisible();
  await expect(page.getByText("Подтверждён", { exact: true })).toBeVisible();
});

test("staff can filter overdue rentals and approve an extension request", async ({ page }) => {
  let currentOrder: AdminOrder = {
    ...orderFixture,
    state: "in_progress",
    planned_end_date: "2030-01-12",
    is_overdue: true,
    overdue_days: 2,
    expires_at: null,
    confirmed_at: "2030-01-01T12:05:00+00:00",
    handed_over_at: "2030-01-10T09:00:00+00:00",
    extension_request: {
      request_id: "d9100000-0000-0000-0000-000000000001",
      state: "pending",
      original_end_date: "2030-01-12",
      requested_end_date: "2030-01-14",
      additional_days: 2,
      amount_minor: 98_000,
      currency: "RUB",
      requested_at: "2030-01-12T08:00:00+00:00",
      decided_at: null,
      rejection_reason: null,
    },
  };
  let overdueFilterSeen = false;
  await page.route(/\/api\/admin\/orders\?.*$/, async (route) => {
    const url = new URL(route.request().url());
    if (url.searchParams.get("overdue") === "true") overdueFilterSeen = true;
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ items: [currentOrder], total: 1, limit: 100, offset: 0 }),
    });
  });
  await page.route(
    /\/api\/admin\/orders\/[^/]+\/requests\/extension\/[^/]+\/approve\?.*$/,
    async (route) => {
      expect(route.request().headers()["idempotency-key"]).toMatch(/^admin-extension-approve-/);
      const request = { ...currentOrder.extension_request!, state: "approved" as const };
      currentOrder = {
        ...currentOrder,
        planned_end_date: request.requested_end_date,
        is_overdue: false,
        overdue_days: 0,
        extension_request: request,
      };
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ order: currentOrder, request, replayed: false }),
      });
    },
  );
  page.on("dialog", (dialog) => void dialog.accept());

  await page.goto("/admin");
  await page.getByRole("button", { name: "Войти в demo-панель" }).click();
  await page.getByLabel("Статус", { exact: true }).selectOption("overdue");
  await expect.poll(() => overdueFilterSeen).toBe(true);
  await expect(page.getByText("Просрочен · 2 дн.", { exact: true })).toBeVisible();
  await page.getByText("Синтетическая фотолампа", { exact: true }).click();
  await expect(page.getByText("Продление до 14 янв. 2030 г.")).toBeVisible();
  await page.getByRole("button", { name: "Проверить и одобрить" }).click();
  await expect(
    page.getByText("Продление одобрено, период аренды и резерв обновлены.", { exact: true }),
  ).toBeVisible();
});

test("staff can create a synthetic phone order without an SMS integration", async ({ page }) => {
  let createdOrder: AdminOrder | null = null;
  await page.route(/\/api\/admin\/orders\?.*$/, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        items: createdOrder ? [createdOrder] : [],
        total: createdOrder ? 1 : 0,
        limit: 100,
        offset: 0,
      }),
    });
  });
  await page.route(/\/api\/admin\/orders\/phone\?.*$/, async (route) => {
    const request = route.request();
    expect(request.headers()["idempotency-key"]).toMatch(/^admin-phone-/);
    const body = request.postDataJSON() as {
      phone_e164: string;
      recipient_name: string;
      start_date: string;
      end_date: string;
      model_id: string;
      branch_id: string;
    };
    expect(body.phone_e164).toBe("+70000000000");
    expect(body.recipient_name).toBe("Тестовый клиент");
    createdOrder = {
      ...orderFixture,
      order_id: "d9000000-0000-0000-0000-000000000099",
      state: "confirmed",
      source: "phone",
      identity_verified: false,
      expires_at: null,
      confirmed_at: "2030-01-01T12:05:00+00:00",
      snapshot: {
        ...orderFixture.snapshot,
        model_id: body.model_id,
        branch_id: body.branch_id,
        start_date: body.start_date,
        end_date: body.end_date,
        quote: {
          ...orderFixture.snapshot.quote,
          model_id: body.model_id,
          branch_id: body.branch_id,
          start_date: body.start_date,
          end_date: body.end_date,
        },
      },
      planned_end_date: body.end_date,
    };
    await route.fulfill({
      contentType: "application/json",
      status: 201,
      body: JSON.stringify({ order: createdOrder, replayed: false }),
    });
  });
  await page.route(/\/api\/admin\/orders\/[^/]+\/contact\?.*$/, async (route) => {
    expect(route.request().headers()["idempotency-key"]).toMatch(/^admin-contact-/);
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        contact: {
          phone_e164: "+70000000000",
          recipient_name: "Тестовый клиент",
          delivery_address: null,
        },
        replayed: false,
      }),
    });
  });
  await page.route(
    /\/api\/admin\/orders\/[^/]+\/(handover|return|inspection)\?.*$/,
    async (route) => {
      const action = new URL(route.request().url()).pathname.split("/").at(-1);
      expect(route.request().headers()["idempotency-key"]).toMatch(`admin-${action}-`);
      const current = createdOrder ?? orderFixture;
      if (action === "handover") {
        createdOrder = {
          ...current,
          state: "in_progress",
          handed_over_at: "2030-01-01T12:10:00+00:00",
          updated_at: "2030-01-01T12:10:00+00:00",
        };
      } else if (action === "return") {
        createdOrder = {
          ...current,
          state: "returned",
          returned_at: "2030-01-03T12:10:00+00:00",
          updated_at: "2030-01-03T12:10:00+00:00",
        };
      } else {
        expect(route.request().postDataJSON()).toEqual({ outcome: "passed" });
        createdOrder = {
          ...current,
          state: "completed",
          completed_at: "2030-01-03T12:15:00+00:00",
          inspection_outcome: "passed",
          updated_at: "2030-01-03T12:15:00+00:00",
        };
      }
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ order: createdOrder, replayed: false }),
      });
    },
  );
  page.on("dialog", (dialog) => void dialog.accept());

  await page.goto("/admin");
  await page.getByRole("button", { name: "Войти в demo-панель" }).click();
  await page.getByText("Новый заказ по телефону", { exact: true }).click();
  await expect(page.getByRole("textbox", { name: "Телефон" })).toHaveValue("+70000000000");
  await expect(page.getByText("SMS выключен", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Создать подтверждённый заказ" }).click();
  await expect(
    page.getByText("Синтетический заказ по телефону создан и подтверждён.", { exact: true }),
  ).toBeVisible();
  await page.getByText("Синтетическая фотолампа", { exact: true }).click();
  await expect(page.getByText("Заказ по телефону", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Показать контакт" }).click();
  await expect(page.getByRole("link", { name: "+70000000000" })).toBeVisible();
  await page.getByRole("button", { name: "Выдать клиенту" }).click();
  await expect(page.getByText("Передача отмечена. Прибор числится у клиента.")).toBeVisible();
  await page.getByText("Синтетическая фотолампа", { exact: true }).click();
  await page.getByRole("button", { name: "Принять возврат" }).click();
  await expect(page.getByText("Возврат принят. Прибор ожидает осмотра.")).toBeVisible();
  await page.getByText("Синтетическая фотолампа", { exact: true }).click();
  await page.getByRole("button", { name: "Осмотр пройден" }).click();
  await expect(page.getByText("Осмотр завершён. Прибор снова доступен.")).toBeVisible();
  await expect(page.getByText("Завершён", { exact: true })).toBeVisible();
});

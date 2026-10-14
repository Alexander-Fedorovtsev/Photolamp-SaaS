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
  confirmed_by_staff_user_id: null,
  handed_over_at: null,
  handed_over_by_staff_user_id: null,
  returned_at: null,
  returned_by_staff_user_id: null,
  completed_at: null,
  completed_by_staff_user_id: null,
  inspection_outcome: null,
  created_at: "2030-01-01T12:00:00+00:00",
  updated_at: "2030-01-01T12:00:00+00:00",
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
  await page.route(/\/api\/admin\/team\/[^?]+\?.*$/, async (route) => {
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
  await expect(
    page.getByRole("heading", { name: "Управляйте каталогом без работы с базой." }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Войти в demo-панель" }).click();
  await expect(page.getByRole("heading", { name: "Бережный свет · демо" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Операционная сводка" })).toBeVisible();
  await page.getByLabel("Организация").selectOption({ label: "Солнечный дом · демо" });
  await expect(page.getByRole("heading", { name: "Солнечный дом · демо" })).toBeVisible();
  await expect(page.getByText("DEMO-UFA-01", { exact: true })).toBeVisible();
  await expect(page.getByText("DEMO-KZN-01", { exact: true })).toHaveCount(0);
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
      confirmed_by_staff_user_id: "d5000000-0000-0000-0000-000000000001",
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
    confirmed_by_staff_user_id: "d5000000-0000-0000-0000-000000000001",
    handed_over_at: "2030-01-10T09:00:00+00:00",
    handed_over_by_staff_user_id: "d5000000-0000-0000-0000-000000000001",
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
      confirmed_by_staff_user_id: "d5000000-0000-0000-0000-000000000001",
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
          handed_over_by_staff_user_id: "d5000000-0000-0000-0000-000000000001",
          updated_at: "2030-01-01T12:10:00+00:00",
        };
      } else if (action === "return") {
        createdOrder = {
          ...current,
          state: "returned",
          returned_at: "2030-01-03T12:10:00+00:00",
          returned_by_staff_user_id: "d5000000-0000-0000-0000-000000000001",
          updated_at: "2030-01-03T12:10:00+00:00",
        };
      } else {
        expect(route.request().postDataJSON()).toEqual({ outcome: "passed" });
        createdOrder = {
          ...current,
          state: "completed",
          completed_at: "2030-01-03T12:15:00+00:00",
          completed_by_staff_user_id: "d5000000-0000-0000-0000-000000000001",
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

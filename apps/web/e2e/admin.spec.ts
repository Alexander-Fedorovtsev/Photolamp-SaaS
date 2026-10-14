import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import type { AdminOrder } from "../src/lib/admin";

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
  identity_verified: true,
  payment_status: "not_started",
  expires_at: "2030-01-01T12:15:00+00:00",
  confirmed_at: null,
  confirmed_by_staff_user_id: null,
  created_at: "2030-01-01T12:00:00+00:00",
  updated_at: "2030-01-01T12:00:00+00:00",
} as const satisfies AdminOrder;

test("demo staff session opens a tenant-isolated catalog workspace", async ({ page }) => {
  await page.goto("/admin");
  await expect(
    page.getByRole("heading", { name: "Управляйте каталогом без работы с базой." }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Войти в demo-панель" }).click();
  await expect(page.getByRole("heading", { name: "Бережный свет · демо" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Сводка каталога" })).toContainText("модели");
  await page.getByLabel("Организация").selectOption({ label: "Солнечный дом · демо" });
  await expect(page.getByRole("heading", { name: "Солнечный дом · демо" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Сводка каталога" })).toContainText("приборы");
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
    };
    await route.fulfill({
      contentType: "application/json",
      status: 201,
      body: JSON.stringify({ order: createdOrder, replayed: false }),
    });
  });

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
});

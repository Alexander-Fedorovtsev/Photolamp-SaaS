import { expect, test } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import AxeBuilder from "@axe-core/playwright";
const shots = process.env.PHOTOLAMP_SCREENSHOT_DIR ?? "test-results/browser-regression";

test("home gives a working organization choice and mobile admin route", async ({ page }) => {
  await page.goto("/#rental");
  await expect(page.getByRole("heading", { name: "Выберите организацию" })).toBeVisible();
  await expect(page.getByLabel("Первый день")).toHaveCount(0);
  await expect(
    page.locator("footer").getByRole("link", { name: "Для арендодателя" }),
  ).toBeVisible();
  await page.getByRole("link", { name: "Уфа · Солнечный дом" }).click();
  await expect(page.getByRole("combobox", { name: "Модель", exact: true })).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Точка аренды", exact: true })).toBeVisible();
});

test("dialogs scroll, trap focus, close with Escape and return focus at all sizes", async ({
  page,
}) => {
  await mkdir(shots, { recursive: true });
  await page.goto("/admin");
  await page.getByRole("button", { name: "Войти в demo-панель" }).click();
  for (const width of [390, 768, 1440]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1024 });
    const trigger = page.getByRole("button", { name: "Добавить модель", exact: true });
    await trigger.click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toHaveCount(1);
    await dialog.getByLabel("Название", { exact: true }).fill("Черновик без отправки");
    await page.keyboard.press("Tab");
    expect(await dialog.evaluate((element) => element.contains(document.activeElement))).toBe(true);
    await page.screenshot({ path: `${shots}/dialog-${width}-${test.info().project.name}.png` });
    await page.keyboard.press("Escape");
    await expect(trigger).toBeFocused();
    await page.getByRole("button", { name: "Добавить тариф", exact: true }).click();
    await expect(page.getByRole("dialog")).toHaveCount(1);
    await expect(page.getByRole("dialog").getByLabel("₽ в сутки")).toBeVisible();
    await page.getByRole("dialog").getByRole("button", { name: "Отмена / закрыть" }).click();
    await trigger.click();
    await expect(page.getByRole("dialog").getByLabel("Название", { exact: true })).toHaveValue(
      "Черновик без отправки",
    );
    const violations = await new AxeBuilder({ page })
      .include("dialog[open]")
      .withTags(["wcag2a", "wcag2aa"])
      .analyze();
    expect(violations.violations).toEqual([]);
    await page.keyboard.press("Escape");
  }
});

test("tenant selection survives storefront round-trip and reload", async ({ page }) => {
  await page.goto("/admin");
  await page.getByRole("button", { name: "Войти в demo-панель" }).click();
  const select = page.getByLabel("Организация", { exact: true });
  await select.selectOption("d0000000-0000-0000-0000-000000000001");
  await page.getByRole("link", { name: "Открыть витрину" }).click();
  await page.locator("footer").getByRole("link", { name: "Для арендодателя" }).click();
  await expect(select).toHaveValue("d0000000-0000-0000-0000-000000000001");
  await page.reload();
  await expect(select).toHaveValue("d0000000-0000-0000-0000-000000000001");
});

test("quote explains date limits and clears a previous result", async ({ page }) => {
  await page.goto("/r/demo-kazan#rental");
  const date = (offset: number) => {
    const value = new Date();
    value.setUTCDate(value.getUTCDate() + 400 + offset);
    return value.toISOString().slice(0, 10);
  };
  await page.getByLabel("Первый день").fill(date(0));
  await page.getByLabel("Последний день").fill(date(0));
  await page.getByRole("button", { name: "Проверить даты и стоимость" }).click();
  await expect(page.locator(".planner-error[role=alert]")).toContainText("Минимальный срок — 3");
  await page.getByLabel("Последний день").fill(date(30));
  await page.getByRole("button", { name: "Проверить даты и стоимость" }).click();
  await expect(page.locator(".planner-error[role=alert]")).toContainText("Максимальный срок — 30");
  await page.getByLabel("Последний день").fill(date(2));
  await page.getByRole("button", { name: "Проверить даты и стоимость" }).click();
  await expect(page.getByRole("status")).toContainText("1 350 ₽");
  await page.getByLabel("Последний день").fill(date(3));
  await expect(page.getByRole("status")).not.toContainText("1 350 ₽");
});

test("unavailable invitation never exposes credential creation", async ({ page }) => {
  await page.route("/api/admin/invitations/preview", (route) =>
    route.fulfill({
      status: 409,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Ссылка уже использована, отозвана или истекла." }),
    }),
  );
  await page.goto(`/admin/invite#token=pli_${"a".repeat(48)}`);
  await expect(page.getByRole("alert").filter({ hasText: "уже использована" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Принять приглашение" })).toHaveCount(0);
  await expect(page).toHaveURL(/\/admin\/invite$/);
});

test("real BFF contact access succeeds and cleans its isolated phone order", async ({
  page,
  baseURL,
}) => {
  test.skip(
    !baseURL || !["localhost", "127.0.0.1"].includes(new URL(baseURL).hostname),
    "Mutations are allowed only in the disposable local stack",
  );
  await page.goto("/admin?tenant=demo-ufa");
  await page.getByRole("button", { name: "Войти в demo-панель" }).click();
  await expect(page.getByRole("heading", { name: "Модели оборудования" })).toBeVisible();
  const tenant = "d0000000-0000-0000-0000-000000000001";
  const date = new Date();
  date.setUTCDate(
    date.getUTCDate() +
      500 +
      ["mobile-320", "mobile-430", "desktop"].indexOf(test.info().project.name) * 10,
  );
  const start = date.toISOString().slice(0, 10);
  date.setUTCDate(date.getUTCDate() + 2);
  const headers = {
    Origin: baseURL!,
    "Idempotency-Key": `regression-phone-${crypto.randomUUID()}`,
  };
  const response = await page.request.post(`/api/admin/orders/phone?tenant_id=${tenant}`, {
    headers,
    data: {
      phone_e164: "+70000000000",
      recipient_name: "Тестовый клиент",
      delivery_address: null,
      model_id: "d2000000-0000-0000-0000-000000000001",
      branch_id: "d1000000-0000-0000-0000-000000000001",
      start_date: start,
      end_date: date.toISOString().slice(0, 10),
      fulfillment: "pickup",
    },
  });
  expect(response.status()).toBe(201);
  const { order } = await response.json();
  try {
    await page
      .locator(".admin-orders")
      .getByRole("button", { name: "Обновить", exact: true })
      .click();
    const card = page.locator(".admin-order-card").filter({ hasText: order.order_id.slice(0, 8) });
    await card.locator("summary").click();
    await card.getByRole("button", { name: "Показать контакт" }).click();
    await expect(card.getByRole("link", { name: "+70000000000" })).toBeVisible();
    await mkdir(shots, { recursive: true });
    await card.screenshot({ path: `${shots}/contact-${test.info().project.name}.png` });
  } finally {
    const cancelled = await page.request.post(
      `/api/admin/orders/${order.order_id}/cancel?tenant_id=${tenant}`,
      { headers: { ...headers, "Idempotency-Key": `regression-cancel-${crypto.randomUUID()}` } },
    );
    expect(cancelled.status()).toBe(200);
  }
});

test("a session broadcast clears the previous role and data in another tab", async ({
  page,
  context,
}) => {
  await page.goto("/admin");
  await page.getByRole("button", { name: "Войти в demo-панель" }).click();
  await expect(page.getByRole("heading", { name: "Модели оборудования" })).toBeVisible();
  await context.route("/api/admin/session", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify([
        {
          tenant_id: "d0000000-0000-0000-0000-000000000002",
          slug: "demo-kazan",
          display_name: "Бережный свет · демо",
          timezone: "Europe/Moscow",
          role: "courier",
        },
      ]),
    });
  });
  await context.route(/\/api\/admin\/courier\/tasks\?/, (route) =>
    route.fulfill({ contentType: "application/json", body: '{"items":[]}' }),
  );
  const other = await context.newPage();
  await other.goto("/admin");
  await other.evaluate(() => {
    const channel = new BroadcastChannel("photolamp-staff-session");
    channel.postMessage("changed");
    channel.close();
  });
  await expect(page.getByRole("heading", { name: "Мои задания" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Модели оборудования" })).toHaveCount(0);
  await expect(page.locator(".admin-order-card")).toHaveCount(0);
  await expect(page.getByRole("link", { name: "+70000000000" })).toHaveCount(0);
});

test("existing catalog records have usable edit dialogs and PATCH contracts", async ({ page }) => {
  await page.goto("/admin?tenant=demo-ufa");
  await page.getByRole("button", { name: "Войти в demo-панель" }).click();
  let saved: Record<string, unknown> | null = null;
  await page.route(/\/api\/admin\/catalog\/[^/]+\/[^?]+\?/, async (route) => {
    saved = route.request().postDataJSON();
    await route.fulfill({ contentType: "application/json", body: '{"updated":true}' });
  });
  for (const [section, field, value, expectedKey, expected] of [
    ["models", "Название", "Уточнённое название", "name", "Уточнённое название"],
    ["pricing", "₽ в сутки", "510.50", "amount_minor_per_day", 51050],
    ["assets", "Внутренний код", "QA-ISOLATED-EDIT", "asset_code", "QA-ISOLATED-EDIT"],
    ["branches", "Город", "Тестовый город", "city", "Тестовый город"],
  ] as const) {
    saved = null;
    await page
      .locator(`[aria-labelledby="${section}-title"] article`)
      .first()
      .getByRole("button", { name: /^Редактировать/ })
      .click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel(field, { exact: true }).fill(value);
    await dialog.getByRole("button", { name: "Сохранить изменения" }).click();
    await expect(dialog).toHaveCount(0);
    expect(saved).toHaveProperty(expectedKey, expected);
  }
});

test("login errors and network denial leave a recoverable form", async ({ page }) => {
  await page.route("/api/admin/session", (route) =>
    route.request().method() === "GET"
      ? route.fulfill({ status: 401, contentType: "application/json", body: "{}" })
      : route.abort(),
  );
  await page.goto("/admin");
  const login = page.getByRole("button", { name: "Войти в demo-панель" });
  await login.click();
  await expect(login).toBeEnabled();
  await expect(page.locator(".admin-alert")).toContainText("Демо-вход недоступен");
  await expect(page.getByRole("button", { name: "Войти", exact: true })).toBeEnabled();
});

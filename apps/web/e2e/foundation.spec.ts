import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test("home links to independent demo storefronts", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Меньше хлопот");
  await page.getByRole("link", { name: /Посмотреть демо в Уфе/ }).click();
  await expect(page.getByRole("note")).toContainText("Демонстрационная витрина");
  await expect(page.locator("header")).toContainText("Солнечный дом");
  await expect(
    page.getByRole("heading", { name: "Выберите подходящий формат лампы." }),
  ).toBeVisible();
  await expect(page.locator(".catalog-card")).toHaveCount(2);
  await expect(page.getByRole("heading", { name: "Домашняя LED-фотолампа" })).toBeVisible();
  await expect(page.locator("#catalog")).toContainText("данные из PostgreSQL");
  await expect(page.getByRole("button", { name: "Перейти к бронированию — скоро" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Заказать по телефону — скоро" })).toBeDisabled();
  await page.goto("/r/demo-kazan");
  await expect(page.locator("header")).toContainText("Бережный свет");
  await expect(page.locator("header")).not.toContainText("Солнечный дом");
  await expect(page.locator('[data-accent="mint"]')).toBeVisible();
  await expect(page.locator(".catalog-card")).toHaveCount(1);
});

test("unknown storefront returns 404", async ({ page }) => {
  const response = await page.goto("/r/not-a-tenant");
  expect(response?.status()).toBe(404);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Витрина не найдена");
});

test("mobile layout, keyboard navigation and accessibility", async ({ page }) => {
  for (const path of ["/", "/r/demo-ufa"]) {
    await page.goto(path);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true);
    await page.keyboard.press("Tab");
    await expect(page.getByRole("link", { name: "Перейти к содержимому" })).toBeFocused();
    await page.keyboard.press("Enter");
    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
      .analyze();
    expect(results.violations).toEqual([]);
  }
});

test("reduced motion and manifest shell", async ({ page, request }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/r/demo-ufa");
  expect(await page.evaluate(() => getComputedStyle(document.documentElement).scrollBehavior)).toBe(
    "auto",
  );
  const manifest = await request.get("/manifest.webmanifest");
  expect(manifest.ok()).toBe(true);
  expect((await manifest.json()).start_url).toBe("/");
});

test("web readiness checks the real API", async ({ request }) => {
  const response = await request.get("/api/health/ready");
  expect(response.status()).toBe(200);
  expect(await response.json()).toEqual({ status: "ready" });
  const version = await request.get("/api/version");
  expect(version.ok()).toBe(true);
  const revision = await version.json();
  expect(revision.git_sha).toBe(revision.api_git_sha);
});

test("browser security headers and admin origin guard are enforced", async ({ request }) => {
  const home = await request.get("/");
  expect(home.headers()["content-security-policy"]).toContain("frame-ancestors 'none'");
  expect(home.headers()["strict-transport-security"]).toBe("max-age=31536000");
  expect(home.headers()["cross-origin-opener-policy"]).toBe("same-origin");

  const rejected = await request.post("/api/admin/session/login", {
    headers: { Origin: "https://attacker.example" },
    data: {},
  });
  expect(rejected.status()).toBe(403);
  expect(await rejected.json()).toEqual({ detail: "Same-origin request required" });

  const sameOrigin = await request.post("/api/admin/session/login", {
    headers: { Origin: "http://localhost:3000" },
    data: {},
  });
  expect(sameOrigin.status()).toBe(400);
});

import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test("planning uses server availability and pricing but cannot submit an order", async ({
  page,
}) => {
  await page.goto("/r/demo-kazan");
  const start = new Date();
  start.setUTCDate(start.getUTCDate() + 10);
  const end = new Date(start);
  end.setUTCDate(end.getUTCDate() + 13);
  await page.getByLabel("Первый день").fill(start.toISOString().slice(0, 10));
  await page.getByLabel("Последний день").fill(end.toISOString().slice(0, 10));
  await page.getByLabel("Способ получения").selectOption("pickup");
  await page.getByRole("button", { name: "Проверить даты и стоимость" }).click();
  await expect(page.getByRole("status")).toContainText("14 дн. · самовывоз");
  await expect(page.getByRole("status")).toContainText("Аренда: 6 300 ₽");
  await expect(page.getByRole("status")).toContainText("Расчёт выполнен сервером");
  await expect(page.locator("a[href^='tel:']")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Перейти к бронированию — скоро" })).toBeDisabled();
  await page.getByText("Сколько стоит аренда?", { exact: true }).click();
  await expect(page.locator("details[open]")).toContainText("синтетические демо-тарифы");
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect(results.violations).toEqual([]);
});

test("animation can pause and reduced-motion keeps the full page visible", async ({ page }) => {
  await page.goto("/");
  const pause = page.getByRole("button", { name: "Остановить анимацию" });
  await pause.click();
  await expect(pause).toHaveAttribute("aria-pressed", "true");
  expect(
    await page.locator(".scene-sun").evaluate((el) => getComputedStyle(el).animationPlayState),
  ).toBe("paused");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.reload();
  expect(
    await page.locator(".scene-sun").evaluate((el) => getComputedStyle(el).animationName),
  ).toBe("none");
  await expect(pause).toBeHidden();
  await expect(page.getByRole("heading", { name: "Забота — в деталях сервиса." })).toBeVisible();
});

test("FAQ works without JavaScript and mobile actions reach their sections", async ({
  browser,
  page,
}) => {
  await page.goto("/");
  const mobileNav = page.getByRole("navigation", { name: "Быстрые действия" });
  if (page.viewportSize()!.width < 700) {
    await mobileNav.getByRole("link", { name: "Условия аренды" }).click();
    await expect(page).toHaveURL(/#rental$/);
    await expect(page.getByRole("heading", { name: /Выберите организацию/ })).toBeInViewport();
  }
  const context = await browser.newContext({ javaScriptEnabled: false });
  const staticPage = await context.newPage();
  await staticPage.goto("/");
  await staticPage.getByText("Можно ли уже оформить аренду?", { exact: true }).click();
  await expect(staticPage.locator("details[open]")).toContainText("Мы не принимаем заявки");
  await expect(
    staticPage.getByRole("heading", { name: "Забота — в деталях сервиса." }),
  ).toBeVisible();
  await context.close();
});

import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test("planning controls do not quote a price or submit an order", async ({ page }) => {
  await page.goto("/r/demo-kazan");
  await page.getByLabel("Длительность аренды").selectOption("14");
  await page.getByLabel("Способ получения").selectOption("pickup");
  await expect(page.getByRole("status")).toContainText("14 дней · самовывоз");
  await expect(page.getByRole("status")).toContainText("Тарифы пока не настроены");
  await expect(page.getByRole("status")).not.toContainText("₽");
  await expect(page.locator("a[href^='tel:']")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Оформить аренду — скоро" })).toBeDisabled();
  await page.getByText("Сколько стоит аренда?", { exact: true }).click();
  await expect(page.locator("details[open]")).toContainText("в демо нет вымышленных цен");
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
    await expect(page.getByRole("heading", { name: /Понятные условия/ })).toBeInViewport();
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

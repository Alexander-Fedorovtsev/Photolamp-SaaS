import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

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

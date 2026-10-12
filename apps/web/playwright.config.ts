import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    trace: "retain-on-failure",
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
      ? {
          executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH,
        }
      : {},
  },
  projects: [
    { name: "mobile-320", use: { viewport: { width: 320, height: 780 } } },
    { name: "mobile-430", use: { viewport: { width: 430, height: 900 } } },
    { name: "desktop", use: { viewport: { width: 1440, height: 900 } } },
  ],
});

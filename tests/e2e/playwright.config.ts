import { defineConfig, devices } from "@playwright/test";
export default defineConfig({
  testDir: "./specs", fullyParallel: false, workers: 1, retries: 0,
  use: { baseURL: process.env.E2E_BASE_URL ?? "http://localhost:15173", trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});

import { defineConfig, devices } from "@playwright/test";

/**
 * Browser E2E base for the SKDY RAG Web UI.
 *
 * ``npm run test:e2e`` builds + serves the Next app on a dedicated port
 * (avoiding collisions with a local ``npm run dev`` on 3000) and runs the
 * specs under ``tests/e2e/``.
 *
 * Requires a browser install once: ``npx playwright install chromium``.
 */
export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:3100",
    trace: "on-first-retry",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
  webServer: {
    command: "npm run build && npm run start -- --port 3100",
    url: "http://127.0.0.1:3100",
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
  },
});

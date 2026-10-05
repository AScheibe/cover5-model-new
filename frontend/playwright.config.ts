import { existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests against the real backend (python -m cover5 serve) and the
 * built frontend (frontend/dist), on a fresh demo data root built by
 * scripts/demo_data.py for every run, so the tests are repeatable and never
 * touch the real data/ folder.
 *
 *   npm run e2e                 # builds the frontend, then runs the suite
 *   E2E_DATA_ROOT=/tmp/x E2E_SCREENS=/tmp/shots E2E_PORT=8799 npm run e2e
 */
const PORT = Number(process.env.E2E_PORT ?? 8799);
const DATA_ROOT = process.env.E2E_DATA_ROOT ?? join(tmpdir(), "cover5-e2e");
process.env.E2E_DATA_ROOT = DATA_ROOT;

// Use a preinstalled Chromium when there is one (CI images, sandboxes); otherwise
// Playwright's own (npx playwright install chromium).
const CHROMIUM_CANDIDATES = [
  process.env.PLAYWRIGHT_CHROMIUM_PATH,
  "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
].filter((p): p is string => !!p);
const executablePath = CHROMIUM_CANDIDATES.find((p) => existsSync(p));

export default defineConfig({
  testDir: "./e2e",
  // One server, one data root: the specs run in order and share its state.
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: [["list"]],
  outputDir: "test-results",
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    timezoneId: "America/New_York",
    locale: "en-US",
    viewport: { width: 1440, height: 900 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 }, launchOptions: executablePath ? { executablePath } : {} },
    },
  ],
  webServer: {
    command: `node e2e/serve.mjs`,
    url: `http://127.0.0.1:${PORT}/api/meta`,
    timeout: 120_000,
    reuseExistingServer: false,
    stdout: "pipe",
    stderr: "pipe",
    env: { E2E_PORT: String(PORT), E2E_DATA_ROOT: DATA_ROOT },
  },
});

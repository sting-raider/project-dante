/**
 * Playwright E2E config — Project Dante browser gate (finish plan §22).
 *
 * Topology assumed by this file:
 *   - API  : http://localhost:8000   started externally
 *            (.venv/Scripts/python.exe -m uvicorn project_dante.api.app:app --port 8000)
 *   - Web  : http://localhost:3000   started externally (npm run dev)
 *
 * There is deliberately NO `webServer` auto-start block: the demo story is
 * "two long-lived servers, tests attach to them". Playwright's built-in
 * `reuseExistingServer` behaviour only applies when a command is configured,
 * so instead every spec waits on real readiness itself:
 *   - API reachability via GET /api/health before anything state-changing;
 *   - web reachability via the first page.goto retrying with expect.poll.
 * Run `npx playwright test` against already-running servers; if either is
 * down the specs SKIP with an explicit reason rather than failing noisily.
 */

import { defineConfig, devices } from "@playwright/test";

const API_BASE = process.env.DANTE_API_URL ?? "http://localhost:8000";
const WEB_BASE = process.env.DANTE_WEB_URL ?? "http://localhost:3000";

export default defineConfig({
  testDir: "./tests/e2e",
  // Keep the suite deterministic and serial-friendly: both specs share one
  // seeded catalog and drive the SAME store, so parallel workers would race.
  fullyParallel: false,
  workers: 1,
  retries: 1,
  timeout: 120_000,
  expect: { timeout: 15_000 },
  reporter: [["list"]],
  use: {
    baseURL: WEB_BASE,
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});

/** Shared with the specs so they probe the same API base the config assumes. */
export const API_URL = API_BASE;


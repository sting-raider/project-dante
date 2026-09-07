/**
 * Playwright E2E config — Project Dante browser gate (finish plan §22).
 *
 * Service addresses used by this file:
 *   - API  : http://localhost:8000   (uvicorn)
 *   - Web  : http://localhost:3000   (Next dev server)
 *
 * The config starts both services in CI and reuses an already-running local
 * service when present. Every spec still waits on real readiness itself:
 *   - API reachability via GET /api/health before anything state-changing;
 *   - web reachability via the first page.goto retrying with expect.poll.
 * Run `npx playwright test`; in CI, an unavailable service is a hard failure.
 */

import path from "node:path";
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
  webServer: [
    {
      command: "uv run uvicorn project_dante.api.app:app --host 127.0.0.1 --port 8000",
      cwd: path.resolve(__dirname, "../api"),
      url: `${API_BASE}/api/health`,
      timeout: 120_000,
      reuseExistingServer: !process.env.CI,
    },
    {
      command: "npm run dev",
      cwd: path.resolve(__dirname),
      url: `${WEB_BASE}/buy`,
      timeout: 120_000,
      reuseExistingServer: !process.env.CI,
    },
  ],
});

/** Shared with the specs so they probe the same API base the config assumes. */
export const API_URL = API_BASE;


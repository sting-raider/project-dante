/**
 * Shared E2E helpers — server readiness probes + demo-operator driving.
 *
 * The suite attaches to externally-started servers (see playwright.config.ts).
 * Every spec first calls `requireServers`, which SKIPs (not fails) the run
 * with an actionable message when either process is down.
 */

import { request as pwRequest, test as base, expect } from "@playwright/test";

export const WEB_URL = "http://localhost:3000";
export const API_URL = process.env.DANTE_API_URL ?? "http://localhost:8000";

/** The hybrid-demo operator token the API expects in live-test-mode. */
const OPERATOR_TOKEN = process.env.DANTE_DEMO_OPERATOR_TOKEN ?? "";

export const test = base.extend<{ api: Awaited<ReturnType<typeof makeApi>> }>({
  api: async ({}, use) => {
    const api = await makeApi();
    await use(api);
    await api.dispose();
  },
});

export { expect };

async function makeApi() {
  const ctx = await pwRequest.newContext({
    baseURL: API_URL,
    extraHTTPHeaders: OPERATOR_TOKEN
      ? { "x-demo-operator-token": OPERATOR_TOKEN }
      : {},
  });
  return ctx;
}

async function reachable(url: string): Promise<boolean> {
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(4000) });
    return res.status < 500;
  } catch {
    return false;
  }
}

/**
 * Skip unless BOTH servers answer. Skips carry the exact start commands so a
 * red X is never the first thing a new contributor sees.
 */
export async function requireServers(
  apiCtx: Awaited<ReturnType<typeof makeApi>>,
  skip: (why?: string) => void,
): Promise<void> {
  const webUp = await reachable(`${WEB_URL}/buy`);
  const apiUp = await reachable(`${API_URL}/api/health`);
  if (!apiUp || !webUp) {
    const missing = [
      !apiUp &&
        `API on ${API_URL} (cd apps/api && .venv/Scripts/python.exe -m uvicorn project_dante.api.app:app --port 8000)`,
      !webUp && `web on ${WEB_URL} (cd apps/web && npm run dev)`,
    ]
      .filter(Boolean)
      .join(" and ");
    skip(`server(s) not running: ${missing}`);
  }
}

type Json = Record<string, unknown>;

export async function apiGet(apiCtx: ReturnType<typeof makeApi> extends Promise<infer T> ? T : never, path: string): Promise<Json> {
  const res = await apiCtx.get(path);
  if (!res.ok()) throw new Error(`GET ${path} -> ${res.status()} ${await res.text()}`);
  return (await res.json()) as Json;
}

export async function apiPost(
  apiCtx: ReturnType<typeof makeApi> extends Promise<infer T> ? T : never,
  path: string,
  body?: unknown,
): Promise<{ status: number; json: Json | null }> {
  const res = await apiCtx.post(path, {
    data: body === undefined ? undefined : JSON.parse(JSON.stringify(body)),
  });
  let json: Json | null = null;
  try {
    json = (await res.json()) as Json;
  } catch {
    /* empty body */
  }
  return { status: res.status(), json };
}

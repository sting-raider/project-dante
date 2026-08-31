import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const ALLOWED_OPERATOR_PATHS = [
  /^demo\/reset$/,
  /^demo\/razorpay\/simulate-event$/,
  /^demo\/contracts\/[A-Za-z0-9_-]+\/(?:ship|deliver|replacement-unavailable)$/,
  /^remedies\/[A-Za-z0-9_-]+\/approve$/,
];

type RouteContext = {
  params: Promise<{ segments: string[] }>;
};

function localOperatorToken(): string {
  const configured = process.env.DEMO_OPERATOR_TOKEN?.trim();
  if (configured) return configured;

  // Next loads apps/web/.env.local, while the local API deliberately reads
  // the repository-root .env. Read only this one server-side value so the
  // browser bundle never receives the operator token or gateway credentials.
  try {
    const env = readFileSync(resolve(process.cwd(), "../../.env"), "utf8");
    const line = env
      .split(/\r?\n/)
      .find((candidate) => /^\s*DEMO_OPERATOR_TOKEN\s*=/.test(candidate));
    if (!line) return "";
    const raw = line.slice(line.indexOf("=") + 1).trim();
    if (
      raw.length >= 2 &&
      ((raw.startsWith('"') && raw.endsWith('"')) ||
        (raw.startsWith("'") && raw.endsWith("'")))
    ) {
      return raw.slice(1, -1).trim();
    }
    return raw.trim();
  } catch {
    return "";
  }
}

function sameOrigin(request: Request): boolean {
  const origin = request.headers.get("origin");
  const host = request.headers.get("host");
  if (!origin || !host) return false;
  try {
    return new URL(origin).host === host;
  } catch {
    return false;
  }
}

export async function POST(request: Request, context: RouteContext): Promise<Response> {
  // This convenience bridge is intentionally unavailable in production.
  // Deployed operators must present the token explicitly through the control
  // room, preserving the API's real-test-mode authorization boundary.
  if (process.env.NODE_ENV !== "development") {
    return Response.json({ detail: "operator_bridge_disabled" }, { status: 404 });
  }
  if (!sameOrigin(request)) {
    return Response.json({ detail: "same_origin_required" }, { status: 403 });
  }

  const { segments } = await context.params;
  const operatorPath = segments.join("/");
  if (!ALLOWED_OPERATOR_PATHS.some((pattern) => pattern.test(operatorPath))) {
    return Response.json({ detail: "operator_route_not_allowed" }, { status: 404 });
  }

  const token = localOperatorToken();
  const apiBase = (
    process.env.NEXT_PUBLIC_API_URL ??
    process.env.API_BASE_URL ??
    "http://localhost:8000"
  ).replace(/\/$/, "");
  const incomingBody = await request.text();

  try {
    const upstream = await fetch(`${apiBase}/api/${operatorPath}`, {
      method: "POST",
      headers: {
        ...(incomingBody ? { "content-type": "application/json" } : {}),
        ...(token ? { "x-demo-operator-token": token } : {}),
      },
      body: incomingBody || undefined,
      cache: "no-store",
    });
    return new Response(await upstream.text(), {
      status: upstream.status,
      headers: {
        "content-type": upstream.headers.get("content-type") ?? "application/json",
        "cache-control": "no-store",
      },
    });
  } catch {
    return Response.json({ detail: "operator_bridge_upstream_unreachable" }, { status: 502 });
  }
}

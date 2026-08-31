/**
 * Typed fetch client for the Dante FastAPI backend.
 *
 * Base URL comes from NEXT_PUBLIC_API_URL (set in apps/web/.env.local for
 * deploys). For local/Tailscale browser sessions without a build-time value,
 * derive the API host from the page host so a phone does not call its own
 * localhost. All money fields are integer paise; all timestamps ISO-8601 —
 * see docs/API_CONTRACT.md.
 */

function defaultApiBase(): string {
  if (typeof window === "undefined") return "http://localhost:8000";

  const hostname = window.location.hostname;
  const isLocalNetworkHost =
    hostname === "localhost" ||
    hostname === "127.0.0.1" ||
    hostname === "::1" ||
    hostname.startsWith("10.") ||
    hostname.startsWith("100.") ||
    hostname.startsWith("192.168.") ||
    /^172\.(1[6-9]|2\d|3[01])\./.test(hostname);

  return isLocalNetworkHost
    ? `${window.location.protocol}//${hostname}:8000`
    : "http://localhost:8000";
}

export const API = process.env.NEXT_PUBLIC_API_URL ?? defaultApiBase();

export class ApiError extends Error {
  readonly status: number;
  readonly url: string;

  constructor(status: number, message: string, url: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.url = url;
  }
}

type RequestOptions = {
  /** Abort after this many ms (default 15s). */
  timeoutMs?: number;
  signal?: AbortSignal;
  headers?: Record<string, string>;
};

async function request<T>(
  path: string,
  method: "GET" | "POST",
  body?: unknown,
  options?: RequestOptions,
  baseUrl: string = API,
): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(
    () => controller.abort(),
    options?.timeoutMs ?? 15_000
  );
  // Allow callers to cancel too.
  options?.signal?.addEventListener("abort", () => controller.abort(), {
    once: true,
  });

  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  const url = baseUrl ? `${baseUrl}${normalizedPath}` : normalizedPath;
  try {
    const res = await fetch(url, {
      method,
      headers: {
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
        ...options?.headers,
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
      cache: "no-store",
      signal: controller.signal,
    });

    if (!res.ok) {
      let message = `${res.status} ${res.statusText}`;
      try {
        // FastAPI error shape: {"detail": "..."} | {"detail": [...]}
        const data = (await res.json()) as { detail?: unknown };
        if (typeof data?.detail === "string") message = data.detail;
        else if (Array.isArray(data?.detail)) message = JSON.stringify(data.detail);
      } catch {
        /* non-JSON error body — keep the status-line message */
      }
      throw new ApiError(res.status, message, url);
    }

    return (await res.json()) as T;
  } catch (err) {
    if (err instanceof ApiError) throw err;
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new ApiError(0, "Request timed out or was aborted", url);
    }
    // fetch() network failures (API down) surface as TypeError.
    throw new ApiError(
      0,
      err instanceof Error ? err.message : "Network unreachable",
      url
    );
  } finally {
    clearTimeout(timeout);
  }
}

/** GET a JSON resource. Throws ApiError on non-2xx / network failure. */
export async function apiGet<T>(path: string, options?: RequestOptions): Promise<T> {
  return request<T>(path, "GET", undefined, options);
}

/** POST a JSON body, parse the JSON response. */
export async function apiPost<T>(
  path: string,
  body?: unknown,
  options?: RequestOptions
): Promise<T> {
  return request<T>(path, "POST", body, options);
}

/**
 * POST to a Next.js route on the current web origin. This is used for the
 * development-only operator bridge: browser code never receives the demo
 * operator secret, while the FastAPI endpoint remains token-gated.
 */
export async function appPost<T>(
  path: string,
  body?: unknown,
  options?: RequestOptions,
): Promise<T> {
  return request<T>(path, "POST", body, options, "");
}

/**
 * Resolve to null instead of throwing — for landing-page stat strips and any
 * surface that must degrade gracefully when the API is down.
 */
export async function apiTry<T>(
  path: string,
  method: "GET" | "POST" = "GET",
  body?: unknown,
  options?: RequestOptions
): Promise<T | null> {
  try {
    return await request<T>(path, method, body, options);
  } catch {
    return null;
  }
}

# DEPLOYMENT — Project Dante on Railway + Vercel

> Target topology (master plan §23/§24): **API → Railway** (FastAPI, single
> replica), **web → Vercel** (Next.js 15). Razorpay runs in **Test Mode**
> only — live keys fail closed at process start by design.

---

## 0. Topology at a glance

```
Buyer browser ──► Vercel (Next.js web) ──► Railway (FastAPI API)
                                              │
                     Razorpay Test Mode ◄─────┤ checkout + server calls
                              │               │
                              └──webhooks──►  https://<api>.up.railway.app/api/webhooks/razorpay
```

- Money = integer paise end to end; no floats anywhere.
- The final Railway service uses **managed PostgreSQL**. It is the durable
  source of truth for contracts, webhook claims, audit events, and money
  actions; JSON is not the preferred deployment store.
- Single replica remains the buildathon release posture (§11.4): scale
  vertically only while the demo is in production.
- The JSON snapshot (`DANTE_STORE_BACKEND=json`) is an emergency single-replica
  recovery fallback only. Use it with a mounted volume, never as the normal
  final deployment path.

---

## 1. Deploy the API to Railway

### Click-path

1. Sign in at [railway.com](https://railway.com) → **New Project** → add
   **Database → PostgreSQL**. Keep the database private to the Railway
   project; Railway exposes a managed `DATABASE_URL` variable.
2. **Deploy from GitHub repo** → pick this repository. In the service card →
   ⋯ menu → **Settings**:
   - **Root Directory**: leave empty (repo root); the config below handles paths.
   - **Config file / Start command**: if the dashboard asks, point it at
     `infra/deploy/railway.toml`. Otherwise set the **Custom Start Command**
     to:
     ```
     cd apps/api && uv run uvicorn project_dante.api.app:app --host 0.0.0.0 --port $PORT
     ```
   - **Networking → Generate Domain**: note the public URL
     (`https://<service>.up.railway.app`) — this is your `API_BASE_URL`.
3. **Settings → Healthcheck**: path `/api/ready`, timeout ≥ 60 s. (Railway
   reads `healthcheckPath` from `railway.toml` automatically when the config
   file is used.)
4. **Deploy → Replicas**: exactly **1**. Do not enable autoscaling.
5. **Variables** tab: add the environment variables from §1.1. Set
   `DANTE_STORE_BACKEND=postgres` and set `DATABASE_URL` to the Railway
   reference `${{Postgres.DATABASE_URL}}` — do not copy database credentials.
6. **Deploy** → watch build logs; first deploy takes a few minutes (uv sync).
7. Open `/api/ready` after deployment and require
   `store_backend: "postgres"` before taking traffic.

### 1.1 Environment variables (API / Railway)

| Variable | Secret? | Value / notes |
|---|---|---|
| `APP_ENV` | no | `production` |
| `DEMO_MODE` | no | `true` for the buildathon demo posture |
| `RAZORPAY_KEY_ID` | **SECRET** | `rzp_test_…` from Razorpay Test Mode. Live `rzp_live_…` keys are REJECTED at boot. |
| `RAZORPAY_KEY_SECRET` | **SECRET** | Test-mode key secret (shown once — copy immediately). |
| `RAZORPAY_WEBHOOK_SECRET` | **SECRET** | The secret you type into the Razorpay webhook form (§2). Must match exactly. |
| `DEMO_OPERATOR_TOKEN` | **SECRET** | Required for `/api/demo/*` state changes and human remedy approvals (`/api/remedies/{proposal_id}/approve`) via `X-Demo-Operator-Token`. Empty ⇒ those writes are LOCKED. Generate with `openssl rand -hex 32`. |
| `DANTE_STORE_BACKEND` | no | `postgres` for the release service. `json` is emergency recovery only. |
| `DATABASE_URL` | **SECRET** | Railway reference variable `${{Postgres.DATABASE_URL}}`; keep the Postgres service private. |
| `LLM_PROVIDER` | no | `` (empty) \| `anthropic` \| `openai-compatible` \| `groq` (`groq` uses the OpenAI-compatible adapter). Empty ⇒ deterministic rules engine. |
| `LLM_MODEL` | no | Model name when a provider is configured. |
| `LLM_API_KEY` | **SECRET** | Provider credential. Omit for rules engine. |
| `PUBLIC_APP_URL` | no | Vercel web URL, e.g. `https://dante-web.vercel.app`. Used as the CORS allow-listed origin. |
| `API_BASE_URL` | no | This service's public URL (Razorpay order/checkout callbacks). |
| `DANTE_STORE_PATH` | no | Emergency JSON fallback only; use a mounted path such as `/data/.dante-store.json`. |

### 1.2 CORS

The API allows origins from `settings.public_app_url` plus
`http://localhost:3000`. Set `PUBLIC_APP_URL` to the exact Vercel production
URL (scheme + host, no trailing slash). For Vercel preview deployments add
the preview origin too or test preview features against localhost.

### 1.3 API edge limits

In production, the API applies a process-local rolling-window limiter per client
address: 120 read requests and 30 write requests per 60 seconds. Health and
readiness probes, CORS preflights, and the HMAC-verified Razorpay webhook path
are exempt so provider retries are not throttled. The limiter is intentionally
single-process; deploy one API replica for this buildathon and use a shared
gateway limiter plus general authentication for a multi-replica deployment.

---

## 2. Configure Razorpay Test Mode webhooks

Prereq: a Razorpay account with Test Mode enabled (toggle top-right of the
dashboard).

### Click-path

1. Dashboard → **Account & Settings → Webhooks** (Test Mode must be active) →
   **Add New Webhook**.
2. **Webhook URL**: `https://<your-api>.up.railway.app/api/webhooks/razorpay`
3. **Secret**: generate one (`openssl rand -hex 32`), paste it here AND set it
   as `RAZORPAY_WEBHOOK_SECRET` on Railway (§1.1). The API verifies
   `X-Razorpay-Signature` (HMAC-SHA256 over raw bytes) BEFORE parsing JSON;
   a mismatch is rejected with no side effects.
4. **Active events** — subscribe to exactly these two:
   - `payment.captured` — the ONLY signal that moves a contract to PAID.
   - `refund.processed` — closes the loop on executed refund remedies.
5. **Create Webhook**. Use the **Send Test Sample** button afterwards and
   confirm delivery in **Webhook Attempts** (expect HTTP 200).

Note: the intake also tolerates `refund.completed` aliases and dedupes by
`X-Razorpay-Event-Id`, but only the two events above are required.

---

## 3. Deploy the web app to Vercel

### Click-path

1. Sign in at [vercel.com](https://vercel.com) → **Add New → Project** →
   import this repository.
2. **Framework Preset**: Next.js (auto-detected; `apps/web/vercel.json`
   pins `framework: nextjs` — the file lives inside the web app because
   Vercel reads it from the project root directory, and `rootDirectory` is
   a dashboard/CLI setting, not a supported vercel.json key).
3. **Root Directory**: `apps/web` (set in the dashboard — required so
   Vercel finds both the app and its vercel.json).
4. **Environment Variables**:

| Variable | Secret? | Value / notes |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | no | `https://<your-api>.up.railway.app`. Inlined into the client bundle at BUILD time — changing it requires a rebuild/redeploy. |

Local `next dev` also exposes a development-only, same-origin operator bridge
for the `/demo` control room. It reads only `DEMO_OPERATOR_TOKEN` from the
repository-root `.env` and injects it into an allowlisted set of synthetic
fulfillment and approval calls, so the token never enters browser JavaScript.
The bridge returns 404 in production; deployed operators must enter the token
explicitly or use an authenticated server-side control surface.
| `PUBLIC_APP_URL` | no | Same Vercel URL (informational parity with the API env). |

5. **Deploy**. Note the assigned domain (e.g. `https://dante-web.vercel.app`)
   — it must equal the API's `PUBLIC_APP_URL` or CORS will block browser
   calls.
6. Every push redeploys previews automatically; production tracks the
   default branch.

Docker alternative: `docker build -f infra/docker/Dockerfile.web
--build-arg NEXT_PUBLIC_API_URL=https://<api-host> -t dante-web ./apps/web`.
The API and web build contexts include narrowly scoped `.dockerignore` files;
local env files, runtime snapshots, virtualenvs, dependencies, and build
caches are excluded before a remote builder receives the context.

---

## 4. Post-deploy verification checklist

Run these in order after both deploys land.

1. **Health** — `curl https://<api>/api/health` →
   `200 {"status":"ok", …,"razorpay":"live-test-mode","llm":…}`.
2. **Ready** — `curl -i https://<api>/api/ready` →
   `200 {"ready":true,"store_backend":"postgres","razorpay_mode":
   "live-test-mode","llm_engine":…,"demo_mode":true}`. A `503` here means
   the managed database is unavailable or the schema cannot be reached — fix
   it before taking traffic. Do not accept a JSON-snapshot response as the
   final Railway posture.
   Neither endpoint ever returns secrets.
3. **Webhook delivery** — Razorpay dashboard → Webhooks → Send Test Sample →
   attempt log shows `200`; check Railway logs for the intake line. Then run
   one real Test Mode payment end-to-end and confirm the contract flips to
   PAID via the webhook (server truth), not the browser callback.
4. **Hero flow** — open the web app:
   1. `/buy`: paste hero brief → Compile → offers ranked with visible failures.
   2. Select offer → contract page shows frozen promises + hashes.
   3. Authorize & pay → real Test Mode checkout → PAID arrives via webhook.
   4. `/demo` panel: ship + deliver `wrong_variant` → material breach spreads.
   5. Contract remedy view: replacement tried first, inventory unavailable,
      refund ranks first → policy ALLOW → execute → refund processed.
   6. `/audit/[id]`: full event stream present.
5. **Operator gate sanity** — with real test keys set, any `/api/demo/*`
   state change WITHOUT `X-Demo-Operator-Token` must be refused; WITH the
   token it succeeds.
6. **Secret hygiene** — `GET /api/ready` and `/api/health` responses contain
   no key ids, secrets, or tokens; Railway/Vercel dashboards show secrets
   masked.

---

## 5. Operations notes

- **Single replica for this release** (§11.4): keep one API replica while the
  demo is in production and scale vertically. Managed Postgres keeps state
  across restarts and deploys; no API volume is required.
- **Emergency recovery**: if Postgres is unavailable, only an operator may
  switch to `DANTE_STORE_BACKEND=json` with a mounted `/data` path. That mode
  must be recorded as a degraded deployment and is not final proof.
- **Rollback**: Railway/Vercel both keep per-deploy snapshots — redeploy the
  previous build from their timelines.
- **Request tracing**: each response exposes `X-Trace-Id` and
  `X-Correlation-Id` (and `X-Contract-Id` on contract paths); the API emits a
  structured `http_request_completed` JSON log without bodies, query strings,
  credentials, or exception text.
- **Local parity**: `.env.example` mirrors every variable above; docker
  images live under `infra/docker/`.

# Browser E2E — Project Dante (finish plan §22)

Playwright suite that drives the real Next.js UI against the real FastAPI
backend. Two specs:

| Spec | Rail | What it proves |
|---|---|---|
| `tests/e2e/hero.spec.ts` | sandbox | The full buyer-owned arc: brief → compiled constraints → offer spread with visible rejections → freeze → contract dossier with MATERIAL PROMISES → authorize → simulated capture delivered as a **real signed webhook** → PAID banner → synthetic ship + wrong-variant delivery → MATERIAL BREACH page → gated remedy pipeline (policy → optional human approval → execute) → REMEDIATED, verified against server truth via the API. |
| `tests/e2e/checkout-options.spec.ts` | live-test-mode (mocked API) | The §3.1 regression guard: intercepts `https://checkout.razorpay.com/v1/checkout.js`, fulfils it with a recording stub (`window.__rzpCtorArgs`), drives a contract to READY_TO_PAY through mocked live-test-mode endpoints, clicks Pay, and asserts the app handed checkout.js `key` (the public key VALUE — **never** a `key_id` option), plus `order_id`, integer-paise `amount` and `currency: "INR"`. Also asserts `.open()` actually ran inside the click's user gesture. |

## Prerequisites

- Python venv at `apps/api/.venv`
- Node dependencies installed for `apps/web`
- Chromium downloaded once: `cd apps/web && npx playwright install chromium`

## Running

Start both servers in separate terminals:

```bash
# terminal 1 — API on :8000
cd apps/api
.venv/Scripts/python.exe -m uvicorn project_dante.api.app:app --port 8000

# terminal 2 — web dev server on :3000
cd apps/web
npm run dev
```

Then:

```bash
cd apps/web
npx playwright test                 # whole suite
npx playwright test tests/e2e/hero.spec.ts            # one spec
npx playwright show-trace test-results/<dir>/trace.zip # debug a failure
```

There is deliberately **no `webServer` auto-start block** in
`playwright.config.ts`: the config assumes both servers are already running and
every spec probes readiness itself. If either process is down the affected spec
**skips with an explicit reason** (including the exact start command) instead of
failing noisily.

### Environment overrides

| Variable | Default | Meaning |
|---|---|---|
| `DANTE_API_URL` | `http://localhost:8000` | API base used by readiness probe + request context |
| `DANTE_WEB_URL` | `http://localhost:3000` | web base used as Playwright `baseURL` |
| `DANTE_DEMO_OPERATOR_TOKEN` | `""` | Value of the `X-Demo-Operator-Token` header. Only needed when the API runs in live-test-mode (real `rzp_test_*` keys configured); without it the demo/fulfillment endpoints are operator-locked and the hero spec skips. |

## Design notes

- **Serial, not parallel**: both specs share the seeded catalog and drive the
  same store; `workers: 1` keeps runs deterministic.
- **Server truth only**: the hero spec treats client-side success as
  meaningless — PAID must arrive through the signed-webhook pipeline
  (`POST /api/demo/razorpay/simulate-event`), and REMEDIATED is confirmed by
  polling `GET /api/contracts/{id}`, never by trusting the DOM alone.
- **Resilient selectors**: roles, accessible names and visible copy
  ("Compile intent", "Authorize & create payment order",
  "Simulate test payment (SANDBOX)", "MATERIAL BREACH") rather than CSS.
  No production component needed data-testid changes.
- **checkout stub**: the stub records every `new Razorpay(options)` argument so
  assertions run against exactly what the app passed to checkout.js — the
  precise surface where the historical `key_id` bug lived.

## Known environmental hazards

- A concurrent `next build` (e.g. another workflow running the frontend gate)
  wipes/replaces `apps/web/.next` under a running dev server and produces
  transient 500s (`Cannot find module './NNN.js'`). If specs skip or fail with
  those signatures, wait for the build to finish, restart `npm run dev`, and
  re-run.

# DEMO SCRIPT — Project Dante (5 minutes)

> Fulfillment events are SYNTHETIC. When `rzp_test_*` keys are configured,
> payment/refund use Razorpay Test Mode; otherwise the clearly-badged sandbox
> adapter is the active rail.

## Pre-flight (before recording)

1. **One process per port — check first.** The API's store is in-memory
   per-process, so a second uvicorn that fails to bind :8000 leaves the
   first process serving an EMPTY store while your browser talks to it —
   contracts vanish ("Unknown contract") and demo calls 404. On Windows:
   `netstat -ano | findstr ":8000 :3000"`, kill stale PIDs
   (`taskkill /PID <pid> /F`) before starting anything.
2. Optional: start `docker compose up -d postgres redis` only when exercising
   the reserved infrastructure. The default JSON-store demo needs neither
   Docker nor Redis.
3. API: `cd apps/api && uv run uvicorn project_dante.api.app:app --port 8000`
4. Web: `cd apps/web && npm run dev`
5. Verify (from the **repository root**, with the API running):
   `apps/api/.venv/Scripts/python.exe scripts/verify_e2e.py` → must print PASSED
6. Browser: open `http://localhost:3000`, keep `/demo` panel open in second tab.

## Shot list

| Time | Page | Action | Narration beat |
|---|---|---|---|
| 0:00–0:25 | `/` | Landing | Thesis: payments remember that you paid; Dante remembers what you paid for. |
| 0:25–1:05 | `/buy` | Paste hero brief, Compile | Intent becomes typed constraints; merchant searched; offers ranked with visible failures. |
| 1:05–1:40 | `/contract/[id]` | Select offer | Promise Ledger freezes warranty/region/delivery; hashes shown; why each promise was material. |
| 1:40–2:25 | contract page | Authorize & pay | Policy ALLOW → order on the active rail (real Razorpay Test Mode or sandbox adapter) → checkout/simulate → signed webhook flips PAID from server truth. |
| 2:25–3:10 | `/demo` then breach | Ship + deliver wrong_variant | Observed facts contradict frozen promises → MATERIAL BREACH spread. |
| 3:10–4:00 | `/contract/[id]/remedy` | Rights graph + remedy | Replacement tried first → inventory unavailable → refund ranks first → policy ALLOW → execute refund. |
| 4:00–4:35 | `/audit/[id]` + tests | Audit trail | Full event stream; webhook chaos + injection tests green. |
| 4:35–5:00 | `/merchant` | Merchant dashboard | What AI buyers couldn't verify; blockers; at-risk GMV. Close. |

## Fallbacks

- If checkout.js is flaky on camera: sandbox simulate button does the same signed-webhook path.
- If live keys absent: say "sandbox adapter, identical signature-verified flow" — honest badge is visible.
- Backup recording: the `verify_e2e.py` terminal output (see pre-flight step 5 for the exact invocation) is your proof.

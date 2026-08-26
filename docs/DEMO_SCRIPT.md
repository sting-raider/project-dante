# DEMO SCRIPT — Project Dante (5 minutes)

> Fulfillment events are SYNTHETIC. Razorpay payment/refund run against Test Mode
> (or the clearly-badged sandbox adapter when no keys are configured).

## Pre-flight (before recording)

1. **One process per port — check first.** The API's store is in-memory
   per-process, so a second uvicorn that fails to bind :8000 leaves the
   first process serving an EMPTY store while your browser talks to it —
   contracts vanish ("Unknown contract") and demo calls 404. On Windows:
   `netstat -ano | findstr ":8000 :3000"`, kill stale PIDs
   (`taskkill /PID <pid> /F`) before starting anything.
2. `docker compose up -d postgres redis`
3. API: `cd apps/api && uv run uvicorn project_dante.api.app:app --port 8000`
4. Web: `cd apps/web && npm run dev`
5. Verify (from `apps/api`, with the API running): `.venv/Scripts/python.exe ../../scripts/verify_e2e.py` → must print PASSED
6. Browser: open `http://localhost:3000`, keep `/demo` panel open in second tab.

## Shot list

| Time | Page | Action | Narration beat |
|---|---|---|---|
| 0:00–0:25 | `/` | Landing | Thesis: payments remember that you paid; Dante remembers what you paid for. |
| 0:25–1:05 | `/buy` | Paste hero brief, Compile | Intent becomes typed constraints; merchant searched; offers ranked with visible failures. |
| 1:05–1:40 | `/contract/[id]` | Select offer | Promise Ledger freezes warranty/region/delivery; hashes shown; why each promise was material. |
| 1:40–2:25 | contract page | Authorize & pay | Policy ALLOW → real Razorpay test order → checkout → webhook flips PAID from server truth. |
| 2:25–3:10 | `/demo` then breach | Ship + deliver wrong_variant | Observed facts contradict frozen promises → MATERIAL BREACH spread. |
| 3:10–4:00 | `/contract/[id]/remedy` | Rights graph + remedy | Replacement tried first → inventory unavailable → refund ranks first → policy ALLOW → execute refund. |
| 4:00–4:35 | `/audit/[id]` + tests | Audit trail | Full event stream; webhook chaos + injection tests green. |
| 4:35–5:00 | `/merchant` | Merchant dashboard | What AI buyers couldn't verify; blockers; at-risk GMV. Close. |

## Fallbacks

- If checkout.js is flaky on camera: sandbox simulate button does the same signed-webhook path.
- If live keys absent: say "sandbox adapter, identical signature-verified flow" — honest badge is visible.
- Backup recording: the `verify_e2e.py` terminal output (see pre-flight step 4 for the exact invocation) is your proof.

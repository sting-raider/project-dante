# Eval report: money_safety_evals

**Status:** `PASS`  ·  **Generated:** 2026-08-26T08:23:20.455199+00:00

## Metrics

| metric | value |
|---|---|
| cases_run | 28 |
| unauthorized_money_actions | 0 |
| case_accuracy | 0.9286 |
| failures | 2 |

## Failures (2)

- **MSF-013** — attempt 0: raised InvalidTransition: Illegal contract transition PAID -> REMEDY_PLANNING
- **MSF-015** — attempt 0: raised InvalidTransition: Illegal contract transition PAID -> REMEDY_PLANNING; only 0 refund effect(s); expected 1 (decisions seen: [])

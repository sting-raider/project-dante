# EXECUTION STATUS — Project Dante Build

**Plan:** PROJECT_DANTE_RAZORPAY_BUILDATHON_MASTER_PLAN.md
**This file is updated after every integration wave.**

## Current phase

**Phase 0 → Wave 1 launch** — foundation committed; specialist agents spawning.

## Frozen contracts (Wave 1 code against these)

- `apps/api/project_dante/domain/types.py` — all Pydantic domain models
- `apps/api/project_dante/domain/state_machine.py` — contract lifecycle transitions
- `apps/api/project_dante/domain/events.py` — event vocabulary + append-only log
- `apps/api/project_dante/domain/hashing.py` — canonical JSON hashing
- `apps/api/project_dante/db/store.py` — record store (`put/get/update/find/list/reset`)
- `apps/api/project_dante/settings.py` — env config

Record-type prefixes live in `db/store.py::TYPE_PREFIXES`.

## Workstream ownership (Wave 1)

| Agent | Owns | Status |
|---|---|---|
| Lead (this) | root configs, api/app.py assembly, integration | running |
| B Razorpay | `integrations/razorpay/**`, payment/refund/webhook routes | spawning |
| C Agents | `agents/**`, intent+offer routes | spawning |
| D Promises | `domain/promises/**`, evidence pipeline | spawning |
| E Rights | `domain/rights/**`, `domain/remedies/**`, policy engine | spawning |
| F Merchant | `integrations/merchant/**`, catalog fixtures + seed | spawning |
| G UI system | `apps/web` design tokens, layout primitives | spawning |
| H Buyer UI | `/buy`, contract page, checkout | spawning |
| I Rights UI | breach/rights/remedy/timeline/audit/merchant pages | spawning |
| J Evals | `evals/**`, synthetic fixtures, benchmark runner | spawning |
| K Security | adversarial fixtures, security tests, THREAT_MODEL.md | spawning |

## Integration rules

1. No two agents edit the same file. Route registration happens in `api/app.py` by the lead only.
2. Specialists import domain types from `project_dante.domain.types` — never redefine.
3. Money is integer paise. Always.
4. Synthetic events carry `"synthetic": true`.
5. Handoffs go to `docs/handoffs/<agent>.md`.

## Exit criteria tracker

- [ ] intent → selected offer → frozen contract → Razorpay order
- [ ] real Test Mode payment → PAID from server truth
- [ ] PAID → delivered(wrong variant) → BREACH → eligible remedies
- [ ] breach → policy ALLOW → real test refund → REMEDIATED
- [ ] audit trail complete for the above

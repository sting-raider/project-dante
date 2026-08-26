# AUTONOMOUS STATUS — Finish-to-Submission Run

**Plan:** PROJECT_DANTE_AUTONOMOUS_FINISH_PLAN.md
**Started:** from main @ ed5be75 (green sandbox baseline: 352 tests, evals PASS, E2E PASS)
**Mode:** continuous lead loop, no phase stopping points (§0.2)

## Active workstreams

| Stream | Scope | Status |
|---|---|---|
| Real-integration workflow (wf_ce50ce48) | checkout `key` fix + two-stage pay UX; HYBRID_TEST_DEMO operator token; X-Refund-Idempotency + timeout tests; openai-compatible provider + honest health; REAL_INTEGRATION_STATUS.md + verify script | RUNNING (5 impl agents + reviewers) |
| Config foundation | live-key hard-reject, razorpay_mode rename, llm_engine | DONE (committed) |
| Persistence | Postgres JSONB store behind DANTE_STORE_BACKEND | DISPATCHING |
| Browser E2E | Playwright sandbox arc + checkout-options stub test | DISPATCHING |
| Merchant surface | /profile machine-readable capabilities + freeze endpoint | DISPATCHING |
| Demo orchestrator | resumable 15-step console state | DISPATCHING |
| Deployment + submission | Railway/Vercel prep, SUBMISSION.md, BLOCKERS.md | QUEUED |
| Final review assault | 10 independent reviewers per plan §42 | QUEUED |

## BLOCKERS requiring human input (docs/BLOCKERS.md maintained)

- Razorpay Test Mode keys (`rzp_test_*`) + webhook secret → needed for requirement 5
  (real smoke proof) and all deployment steps that touch money.
- LLM API key if a model-backed demo run is required.
- Deployment account authorization if environment cannot perform it.

Everything else proceeds without input.

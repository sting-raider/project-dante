# SUBMISSION — Project Dante

> Ready-to-paste values for the Razorpay AI Buildathon submission form (finish plan §38).
> Every claim below is scoped to what actually runs. See the honest-systems statement
> at the bottom before editing anything.

## Form fields (exact values)

### Project name

```text
Project Dante
```

### Track

```text
Razorpay AI Buildathon — Track 1: AI Growth & Agentic Commerce
```

### Objective  (finish plan §39, improved draft)

```text
Build the missing half of agentic commerce: a buyer-owned runtime that carries a
purchase beyond checkout. Dante compiles a natural-language buying brief into typed
constraints, selects a merchant offer under hard constraints, freezes the exact
promises that made the offer acceptable into a hashed contract, pays through
Razorpay, observes fulfillment reality, detects material breaches against the
frozen promises, derives the buyer's rights, and executes policy-gated remedies —
with real Razorpay Test Mode refunds when keys are configured, and an append-only audit trail of
every step. Payments remember that you paid; Dante remembers what you paid for,
and what to do when the box betrays the promise.
```

### What does it solve?  (finish plan §40, improved draft)

```text
Agentic commerce today stops at the payment success tick. Payment systems remember
that you paid — never why you chose the product, which promises made the offer
acceptable (warranty type and region, delivery date, condition, price band), or
what happened when the box arrived. When reality diverges from what was promised,
the buyer's evidence, rights, and remedies must be reconstructed after the fact,
by a human, from screenshots.

Dante captures all of it at purchase time and stays alive until the purchase is
satisfied or remediated:

· Intent becomes typed constraints — price caps, warranty type + region, delivery
  deadlines, category — parsed deterministically (critical-constraint recall 1.0
  on a 68-case eval suite).
· Offers are evaluated against hard constraints that are absolute: zero
  hard-constraint-violating selections across 117 feasibility checks; rejected
  offers stay visible with their exact failure reasons.
· The winning offer's promises are frozen into a hashed contract, each promise
  linked to evidence and marked material/non-material relative to the original
  intent. Untrusted merchant text can add claims but never override structured data.
· Payment runs through Razorpay (order creation, Standard Checkout, signature
  verification, raw-body HMAC webhooks — idempotent and out-of-order safe).
· Synthetic, clearly-labeled fulfillment feeds observed facts to a promise
  verifier: a wrong variant shipped against a manufacturer-warranty promise
  becomes a MATERIAL BREACH with reason codes.
· A Purchase Rights Graph turns entitlements from four issuers (merchant,
  manufacturer, payment provider, promotion) into nodes with eligibility, expiry,
  required evidence, dependencies, blocks, and fallbacks.
· A deterministic remedy planner ranks candidates by a visible scoring function
  (replacement tried first, refund next), a financial policy engine makes the
  ALLOW / REQUIRE_APPROVAL / DENY call, and an idempotent refund executes
  behind the same gates — implemented and verified end-to-end against the
  built-in sandbox rail, and wired to execute as real Razorpay Test Mode calls
  whenever `rzp_test_*` keys are configured (real-gateway proof is tracked in
  REAL_INTEGRATION_STATUS.md). Replay-safe to exactly one money effect.

The result for Track 1: agentic commerce that earns repeat trust. Buyers get an
auditable memory of every purchase and autonomous, bounded remediation; merchants
get a machine-readable view of what AI buyers couldn't verify — the blocker
distribution behind lost agentic GMV.
```

### Biggest build challenges  (finish plan §41, improved draft)

```text
1. Webhook-only payment truth. PAID can never be granted from a client callback.
   The state machine accepts the transition only from a server-side webhook whose
   raw bytes pass HMAC-SHA256 verification before parsing; forged and tampered
   webhooks get 401 with zero domain effect, and duplicate/out-of-order deliveries
   reconcile to the same terminal state. A redelivered capture even resumes an
   interrupted walk to PAID.

2. Keeping LLM agents away from money. Agents compile, rank, extract, propose —
   they never decide or execute. Deterministic services validate constraints,
   hashes, transitions, amounts, currency, idempotency, and ownership; the policy
   engine owns ALLOW / REQUIRE_APPROVAL / DENY and the executor re-validates
   immediately before any Razorpay call. 28 adversarial proposals (negative
   amounts, one-paise-over refunds, string/float amounts, int64 overflow,
   injected reason codes, cross-contract substitution, 10× replay) produced
   zero unauthorized money actions.

3. Prompt-injection containment with extraction pressure. Merchant listing text
   is untrusted, but the system must still read claims from it. A narrow scanner
   lets only product-claim keys through as unverified data; structured
   warranty.type can never be flipped by text. All 50 adversarial corpus payloads
   (fake [SYSTEM] markers, tool-call forgery, homoglyph/fullwidth obfuscation,
   base64 smuggling, multilingual variants) are treated as data — violations: 0.

4. Idempotent refunds under replay. Every money action carries a derived
   idempotency key (project-dante:{contract}:{remedy}:v1), a policy snapshot hash,
   reason codes, and evidence IDs. Replaying execute returns the identical
   refund id — exactly one refund effect, verified by dedicated network-level
   tests (rfnd_-shaped refund ids from the real gateway in Test Mode when keys are
   configured; sandbox-adapter ids otherwise, badged as such).

5. Making evaluation drive the build. A 147-case eval harness (intent, offers,
   breaches, money safety) ran continuously and caught real integration bugs:
   UTC-vs-local clock skew flipping delivery feasibility daily, dropped
   price-band floors, mouse/mice category mismatch, refurbished units passing
   new-only intents, inventory ignored by the evaluator. All fixed; suites PASS.

6. Honest dual-mode operation. The same code paths run against real Razorpay
   Test Mode keys and, absent keys, against a built-in sandbox adapter with
   genuinely computed HMAC signatures — badged SANDBOX in every surface so the
   demo never lies about which gateway it talked to.
```

### Links

| Field | Value |
|---|---|
| GitHub repository | https://github.com/sting-raider/project-dante |
| Live demo | `[LIVE_DEMO_URL]` — TBD post-deploy (see docs/BLOCKERS.md §3) |
| Video | `[VIDEO_URL]` — TBD post-recording (shot list: docs/PITCH.md; capture checklist: docs/SCREENSHOTS.md) |

---

## Honest-systems statement (keep verbatim wherever the project is described)

- **Payments and refunds hit the real gateway only when keys are configured.**
  With Test Mode keys (`rzp_test_*`) present, order creation, Standard
  Checkout, signature verification, webhooks, and refunds are real Razorpay
  API calls. Without keys, a built-in **sandbox adapter** produces realistic
  IDs through genuinely computed HMAC signature flows, and every surface is
  badged **SANDBOX**; the health endpoint reports `sandbox-adapter` vs
  `live-test-mode`. The sandbox-verified arc is proven by tests and
  `scripts/verify_e2e.py`; historical single-line Test Mode evidence is recorded
  criterion-by-criterion; the amended exact two-line LLM basket proof remains
  NOT_YET_PROVEN in
  `REAL_INTEGRATION_STATUS.md`. Live
  (`rzp_live_*`) keys are hard-rejected at startup.
- **Fulfillment is synthetic and labeled.** Ship, delivery, and device-metadata
  observations come from a simulator; every record carries `"synthetic": true`,
  the UI renders SYNTHETIC badges, and the demo endpoints require an operator
  token. Nothing pretends a courier existed.
- **Deterministic money authority.** LLM agents propose; deterministic code
  disposes. Amounts, transitions, idempotency, and execution are owned by the
  rules engine, policy engine, and executor — never by a model.
- **No AP2/UCP claims.** Dante implements none of those protocols and asserts no
  compliance with them; the prior-art section of the README positions the work
  adjacent to them instead.
- **No statutory/legal-right reasoning.** Entitlements are demo-defined purchase
  rights from four issuer types; no consumer-law analysis is performed.
- **Known limits stated plainly:** one fictional merchant (Aster Electronics,
  112 SKUs, static fixture), managed PostgreSQL is the final Railway store with
  JSON retained only as an emergency fallback, one payment provider, outcome
  verification only as strong as the available evidence.
- Proof discipline: real-gateway claims are tracked criterion-by-criterion in
  `REAL_INTEGRATION_STATUS.md` and flip to PROVEN only when
  `scripts/verify_real_integration.py` observes them. The final row cannot be
  upgraded by hand: it requires persisted `engine=llm` evidence for the exact
  two-line basket.

## Submission-day checklist

1. Fill `[LIVE_DEMO_URL]` after Railway/Vercel deploy; confirm health shows
   `live-test-mode` and UI badges flip accordingly.
2. Record video per docs/PITCH.md; fill `[VIDEO_URL]`.
3. Capture 11 screenshots per docs/SCREENSHOTS.md; refresh README table.
4. Re-run gates: pytest suite, eval runners, `scripts/verify_e2e.py`,
   `cd apps/web && npx tsc --noEmit && npm run build`.
5. With the Test Mode keys and LLM provider configured: run
   `scripts/verify_real_integration.py`; it appends its own BEGIN-RUN evidence
   block to `REAL_INTEGRATION_STATUS.md`.

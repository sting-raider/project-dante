# PITCH — Project Dante

> Two deliverables: a 60-second spoken flow for judges who only listen, and the
> full 4:30–4:50 video shot list (16 shots) with narration. Narration lines that
> touch money or fulfillment are written to be **honest on their face**: with
> real Test Mode keys configured, money actions are real Razorpay API calls;
> without keys they run through the badged **SANDBOX** adapter — say which rail
> is live when you record. Fulfillment is synthetic and labeled either way, and
> agents propose while deterministic code disposes. Real-gateway proof status:
> REAL_INTEGRATION_STATUS.md (all criteria NOT_YET_PROVEN until keys run).
>
> Pre-flight and fallbacks: docs/DEMO_SCRIPT.md. Capture checklist per shot:
> docs/SCREENSHOTS.md.

---

## Part 1 — the 60-second flow

One paragraph, spoken at normal pace (~150 wpm). Memorize the beats, not the words.

> Payments remember that you paid. They never remember what you paid *for* —
> which promises made the offer acceptable, or what to do when the box betrays
> them. Project Dante is a buyer-owned agentic commerce runtime that closes that
> gap. You give it one sentence — "over-ear ANC headphones under twelve thousand
> rupees, Indian manufacturer warranty, delivered in three days." Dante compiles
> it into typed constraints, searches a 112-SKU merchant catalog, and ranks offers
> under hard constraints — rejected offers stay visible with their failure
> reasons. The winner's promises are frozen into a hashed contract, each promise
> linked to evidence and marked material to your intent. Then Dante pays — with
> Test Mode keys configured this is a real Razorpay order through Standard
> Checkout, otherwise the badged sandbox adapter stands in — and only a
> signature-verified webhook can flip it to paid; no client message can.
> Fulfillment here is simulated — every event is labeled SYNTHETIC on screen.
> When the wrong variant arrives, observed facts contradict the frozen promises:
> MATERIAL BREACH, automatically. Dante derives your rights from four issuers,
> ranks remedies — replacement first, refund next — and a deterministic financial
> policy engine, never an LLM, makes the ALLOW call before an idempotent refund
> executes behind the same gates. Replay it ten times: exactly one refund. Every
> step sits on an append-only audit trail. Agents propose; deterministic code
> disposes.

**If you have only 15 seconds:** "Dante remembers what you paid *for*. Promises
frozen at purchase, breach detected against them, rights derived, an idempotent
refund executed behind a deterministic policy gate — fully audited."

---

## Part 2 — video shot list (16 shots, target runtime 4:30–4:50)

Structure: the 16 shots mirror `scripts/verify_e2e.py`'s `[01]…[16]` step arc
one-to-one, so the terminal backup recording and the video tell the same story.
Timestamps are targets; ±5s per shot is fine, total must stay under 5:00.

Narration conventions used throughout:
- **[REAL]** = say explicitly that this touches the real gateway in Test Mode
  — only true when `rzp_test_*` keys are configured; otherwise name the SANDBOX
  badge and keep talking (the same code path runs either way).
- **[SYNTHETIC]** = say explicitly this is simulated and labeled as such on screen.
- **[DETERMINISTIC]** = say explicitly that code, not a model, made this decision.

| # | Time | Page / view | On-screen action | Narration |
|---|---|---|---|---|
| 1 | 0:00–0:18 | `/` landing | Slow scroll down the masthead; rest on the thesis line | "Payments remember that you paid. Dante remembers what you paid *for* — the intent, the promises that made the offer acceptable, and what happens when reality breaks them. This is a buyer-owned agentic commerce runtime, built for Track 1." |
| 2 | 0:18–0:35 | `/buy` | Paste the hero brief verbatim; cursor blinks; click Compile | "It starts as one sentence. Watch what stays honest throughout: when test keys are configured, money actions are real Razorpay **Test Mode** calls; otherwise the same flow is visibly **SANDBOX**. Fulfillment is simulated and labeled, and every money-touching decision is made by deterministic code — not by a model." |
| 3 | 0:35–0:55 | `/buy` — Buying Brief column | Highlight typed constraints appearing (price cap ₹12,000 · category · ANC · over-ear · warranty type + region IN · delivery deadline) | "The brief compiles into typed constraints — price cap, category, ANC, warranty type *and* region, a delivery deadline. Deterministic parsing, schema-checked; the optional LLM path is never allowed to invent a constraint." |
| 4 | 0:55–1:15 | `/buy` — offer spread | Scroll the ranked spread; point out two red infeasible cards with failure reasons; land on the hero SKU | "Offers rank under hard constraints — and hard means absolute. These rejected cards stay visible with their exact failure reasons. Zero constraint-violating selections across our 117-check eval suite. That's the bar the evaluator is held to." |
| 5 | 1:15–1:35 | `/contract/[id]` — dossier | Page loads frozen contract; hover the Promise Ledger rows showing hashes and material flags | "Selecting an offer freezes its promises into a hashed contract — warranty type, region, delivery date — each promise linked to evidence and marked material because my intent demanded it. Untrusted listing text can add claims; it can never overwrite these." |
| 6 | 1:35–1:50 | `/contract/[id]` — authorization card | Show authorization envelope bound to the contract hash | "I authorize against this exact hash. If the contract drifts after freeze, this authorization is void by construction — the binding is checked again right before any payment executes." |
| 7 | 1:50–2:15 | `/contract/[id]` — Razorpay panel → Standard Checkout | Policy chip ALLOW; click pay; complete checkout with UPI `success@razorpay` or a domestic Indian Test Mode card in the Razorpay window | "With Test Mode keys in, **this is a real order** through the Razorpay Orders API and Standard Checkout — if you're seeing the SANDBOX badge instead, say so aloud: same code path, local adapter. And note what flips the status either way: only a server-to-server webhook whose raw bytes pass HMAC signature verification. No client callback can ever mark this paid." |
| 8 | 2:15–2:30 | `/contract/[id]` — PAID + timeline entry | Status chip flips to PAID; open timeline row showing webhook-confirmed capture with payment id | "Webhook verified, payment captured — there's the payment id on the contract. If the webhook arrives twice, or out of order, reconciliation still lands exactly here. Duplicate-safe, replay-safe." |
| 9 | 2:30–2:50 | `/demo` panel | Click Ship; then Deliver with scenario `wrong_variant`; SYNTHETIC badges visible | "Now fulfillment. To be explicit: **this part is simulated.** Every ship and delivery event carries a synthetic flag, and you can see the SYNTHETIC label on screen. I'm delivering the wrong variant — a different SKU than the one promised." |
| 10 | 2:50–3:10 | `/contract/[id]/breach` | Giant MATERIAL BREACH headline; PROMISED vs OBSERVED columns side by side | "Observed facts meet the frozen promises, and the verifier compares them key by key. Wrong variant against a manufacturer-warranty promise: MATERIAL BREACH, derived automatically, with reason codes. Our breach suite runs at F1 1.0 with zero false positives." |
| 11 | 3:10–3:30 | `/contract/[id]/rights` | SVG rights graph renders; click the manufacturer entitlement node → drawer opens | "The breach activates the Purchase Rights Graph — entitlements from four issuers: merchant, manufacturer, payment provider, promotion. Each node knows its eligibility, expiry, required evidence, what blocks it, and where it falls back. One honest boundary: these are demo-defined purchase rights, not statutory legal claims." |
| 12 | 3:30–3:50 | `/contract/[id]/remedy` | Ranked candidates with score bars (value .40 / intent-restoration .35 / speed .15 / inconvenience −.10); replacement shown blocked | "Remedies rank on a visible scoring function — replacement tries first. Here replacement inventory is unavailable, so it's blocked, not hidden. Refund ranks next." |
| 13 | 3:50–4:10 | `/contract/[id]/remedy` — policy + execute | Policy decision card ALLOW with policy ids and reason codes; click Execute; refund id appears | "Here's the line we consider inviolable: agents propose, **deterministic code disposes**. A financial policy engine — rules, not a model — owns ALLOW, REQUIRE_APPROVAL, DENY, and re-validates amount and ownership immediately before the call. With keys configured this refund is a real Razorpay Test Mode API call; on the sandbox rail it's the same signed flow against the local adapter — the badge tells you which. Either way it carries reason codes, evidence ids, and an idempotency key." |
| 14 | 4:10–4:25 | `/contract/[id]/remedy` — idempotency proof | Click Execute again (or show the replay test output); identical refund id, one money action row | "Watch the replay: executing again returns the *identical* refund id. Ten replays in our adversarial tests — exactly one money effect. That's what 'deterministic money authority' means in practice." |
| 15 | 4:25–4:40 | `/audit/[id]` then `/merchant` | Scroll the append-only event trace; cut to merchant dashboard masthead "What your AI buyers couldn't verify" with blocker bars | "Every step of this arc lives on an append-only audit trail — events, hashes, decisions, ids. And the merchant gets something too: a machine-readable view of what AI buyers couldn't verify — the blocker distribution behind lost agentic demand." |
| 16 | 4:40–4:55 | `/` landing thesis + terminal | Cut between the thesis line and the tail of `verify_e2e.py`: `[16] audit … E2E VERIFICATION PASSED` | "Payments remember that you paid. Dante remembers what you paid for — and holds the whole arc from intent to refund to audit. Full verification script in the repo prints all sixteen steps green; try it yourself. Project Dante — buyer-owned commerce, honest about every layer." |

### Recording notes

- Total narration above ≈ 700 words ≈ 4:45 at demo pace. Trim shot 11 or 15
  first if you run long; never trim the [REAL]/[SYNTHETIC]/[DETERMINISTIC]
  qualifiers — they are load-bearing honesty, not decoration.
- Before recording, check the health endpoint once: `live-test-mode` means
  shots 7/13/13's "real Razorpay" lines are true as scripted; `sandbox-adapter`
  means name the SANDBOX badge instead (shot 7's narration already covers it).
- Say "Test Mode" at least three times (shots 2, 7, 13). Say "synthetic /
  simulated" at least twice (shots 2, 9). Say "deterministic code disposes" once
  verbatim (shot 13).
- Backup proof if checkout.js misbehaves on camera: the sandbox simulate button
  drives the same signed-webhook path (docs/DEMO_SCRIPT.md fallbacks); keep the
  SANDBOX badge in frame and name it aloud.

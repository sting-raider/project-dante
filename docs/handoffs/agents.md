# Handoff — Agent C (Agent Runtime: provider, intent compiler, offer evaluator, intents routes)

## Goal

Build the buyer-side agent runtime for Project Dante: a typed model-provider
abstraction with Anthropic and OpenAI-compatible structured-output
implementations (including the Groq alias), an
`IntentCompilerAgent` that turns raw buyer text into a validated `BuyerIntent`
(LLM path plus an excellent deterministic rules path), a
deterministic-authority `OfferEvaluatorAgent`, and the `/api/intents/*` routes
from the frozen API contract — with the absolute invariant that no code path
can mark an offer feasible while violating a hard constraint.

## Completed

- **Provider abstraction** (`agents/provider.py`): `ModelProvider` protocol;
  `AnthropicProvider` calling `POST https://api.anthropic.com/v1/messages`
  directly over httpx (headers `x-api-key`, `anthropic-version: 2023-06-01`),
  tool-use-free structured output: schema-only JSON request → pydantic
  validation → one error-feedback retry → `AgentValidationError`. 30s timeout.
  Respects `settings.llm_model`. Transport errors fail fast to the caller's
  fallback. Secrets never enter prompts, logs, or STORE records.
  `DeterministicProvider` raises `NotImplementedError` by design — the rules
  engines are the real fallback. `get_provider()` returns `None` unless
  `settings.llm_enabled`; every caller drops to rules when None.
  `LLM_PROVIDER=groq` selects the OpenAI-compatible implementation with
  Groq's `/openai/v1` base URL supplied through `LLM_BASE_URL`.
- **IntentCompilerAgent** (`agents/compiler.py`): two paths producing the
  identical frozen `BuyerIntent` shape. LLM path uses the §51 prompt
  principles (untrusted-text framing, schema-only output, unknown > invented,
  integer paise). Rules path handles price caps (₹/Rs/INR symbol, comma
  grouping, `12k`, `1.5 lakh`, `12000 rupees`, `<=`, "not over"), category
  keywords (headphones, earbuds, router, laptop, charger, cable, keyboard,
  mouse, monitor, phone), form factor (over-ear/on-ear/earbuds), ANC,
  warranty phrases ("india manufacturer warranty" / "official" / "brand"
  → manufacturer+IN; "seller warranty" → seller), delivery deadlines
  (`by <weekday>` next occurrence, `tomorrow`, `within N days`), brand soft
  preferences (17 brands incl. TP-Link/D-Link alias collapse, boAt casing),
  color/storage variants, and substitution language ("no substitutes" /
  "exactly"). Unknown values are omitted, never invented; contradictions are
  recorded and left for the evaluator to find infeasible. Every compile
  persists the intent record and appends INTENT_RECEIVED + INTENT_COMPILED.
- **OfferEvaluatorAgent** (`agents/evaluator.py`): deterministic core is
  authoritative. Checks every critical constraint against structured offer
  fields (price cap + `max_total_amount_paise`, category contains-style match,
  form_factor, anc, warranty type — where `"unknown"` FAILS a manufacturer
  constraint, warranty region, delivery deadline via promised_by_date OR
  min/max-day window). Hard failures carry `{key, op, expected, actual}`.
  Soft scores: brand preference, relative price, delivery speed, warranty
  duration. Feasible offers rank first by weighted soft total, then cheaper.
  Optional LLM pass rephrases explanations only and can never change
  feasibility or order; disagreement is logged to agent_run. Appends
  OFFER_EVALUATED per evaluation run.
- **Routes** (`api/routes/intents.py`) exactly per contract:
  - `POST /api/intents/compile {raw_text} → {intent, engine}`
  - `POST /api/intents/{id}/search → {intent, results:[{offer, evaluation}],
    items:[{item_id, label, quantity, results, feasible_count,
    recommended_offer_id}], bundle_recommendation, engine}` — merchant
    service search first, STORE offers as fallback; multi-item lines are
    evaluated independently and the deterministic recommendation considers
    quantity-adjusted line amounts under the parent cap. Persists
    `_type=evaluation` summaries; appends CATALOG_SEARCHED.
  - `POST /api/intents/{id}/select-offer {offer_id}` for one item or
    `{items:[{item_id, offer_id}]}` for a bundle → `{contract, promises,
    evidence, _freeze_via}` — requires every line exactly once, rechecks
    feasibility, quantity inventory and the parent cap, then rejects
    infeasible/un-evaluated offers with 409. A hard-constraint violation can
    NEVER be selected. Creates one aggregate contract with frozen
    `line_items[]` through the state machine
    (INTENT_READY→OFFER_SELECTED→CONTRACT_FROZEN), hashes the aggregate and
    appends OFFER_SELECTED + CONTRACT_CREATED.

## Files

- `apps/api/project_dante/agents/provider.py`
- `apps/api/project_dante/agents/compiler.py`
- `apps/api/project_dante/agents/evaluator.py`
- `apps/api/project_dante/api/routes/intents.py`
- `apps/api/tests/test_agents.py` (17 tests)
- `apps/api/tests/test_intent_rules.py` (28 tests)

## Public interfaces

```python
# project_dante.agents.provider
get_provider(settings) -> AnthropicProvider | OpenAICompatibleProvider | None
                       # None => rules engine
class AgentValidationError(Exception)                # after 2 attempts
_log_agent_run(**kwargs)                             # shared agent_run persistence

# project_dante.agents.compiler
get_compiler() -> IntentCompilerAgent                # provider wired from settings
await IntentCompilerAgent.compile(raw_text, trace_id=None) -> BuyerIntent
rule_compile(raw_text) -> BuyerIntent                # pure rules path, no I/O
extract_price_caps / extract_category / extract_attributes /
extract_warranty / extract_delivery / extract_brands  # unit-testable pieces

# project_dante.agents.evaluator
get_evaluator() -> OfferEvaluatorAgent               # provider wired from settings
OfferEvaluatorAgent.evaluate(intent_dict, offers) -> [
    {"offer": dict, "rank": int,
     "evaluation": {"feasible": bool, "hard_failures": [...],
                    "soft_scores": [...], "soft_total": float,
                    "explanation": str}}, ...]
await OfferEvaluatorAgent.enrich_explanations(intent_dict, results) -> results
```

Note for evaluators-of-record: `evaluate()` returns items carrying an extra
`soft_total` key used internally for ranking; the route strips it before
responding so the wire format matches the contract exactly.

## Tests with real results

```
cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_agents.py tests/test_intent_rules.py -q
65 passed in 0.54s

python evals/runners/run_intent_evals.py   (from repo root)
68 cases: critical_recall=1.0, overall_accuracy=1.0, failures=0 -> PASS
```

### Eval-fix round (team-lead reopen; dataset = ground truth)

Baseline after round-1 fixes was critical_recall 0.71; final run is 1.0.
Changes beyond round 1:

- Price caps: word-number amounts now tolerate a trailing currency word
  ("under twelve thousand rupees"), "spending no more than three thousand
  rupees", "budget around 25k"/"around X"; new `extract_price_range` handles
  "between 10k and 15k" -> min_price_paise gte + max_price_paise lte.
- Brands: rewritten (`extract_brands`) — explicit gated mentions ('Zephyr
  brand', 'Aster brand', 'X brands only', 'only/must be/exclusively') become
  HARD `brand` constraints; ungated mentions become SOFT preferences at
  weight 0.8 (was 0.6). The unqualified phrase 'Aster Electronics' remains
  the merchant name and is not invented as a product-brand constraint. 'noise'
  is never a brand token (it only appears in 'noise cancelling'). Multi-brand
  `or` chains preserve the complete accepted set as an ``in`` list; independent
  gated clauses remain separate equality gates.
- Warranty: clause-based proximity matching replaces substring lists — any
  clause containing both 'warrant' and 'manufactur'/'seller' parses in
  either word order; 'India warranty' maps to manufacturer+IN per market
  convention and dataset truth; polarity guard: 'acceptable' / 'does not
  matter' / 'any warranty' near the phrase suppresses type constraints.
- Condition: 'sealed'/'unopened'/'factory-sealed' -> new;
  'refurbished okay' relaxes (no constraint); bare 'refurbished' ->
  refurbished.
- Category: earbuds emit category=headphones (harness treats earbuds as a
  distinct value); 'not necessarily ANC' no longer emits attributes.anc.
- terms.region: 'Indian region stock' -> terms.region eq IN.
- SKU: exact-model language ('specifically want the Aster ANC Pro ...')
  matches catalog titles (hyphen-normalized) from the Aster fixture via
  `_catalog_titles()` and emits sku eq AST-... .
- Delivery: '<weekday> deadline' phrasing; 'by tomorrow evening'.

Dataset notes for Agent J / team-lead: INT-020 now asserts the catalog-resolved
hero SKU in addition to its category/form-factor/ANC facts; INT-055 asserts the
complete two-brand `in` list. Both are exercised by the rules compiler and
evaluator.

Round-1 notes (retained): trailing caps, word-number caps + duration guard,
'bucks tops', warranty order variants, condition phrases, catalog brands,
bare 'over-ears', delivery verbs/qualifiers, 'Do NOT substitute'.

### Offer-eval round (Agent J cross-module findings)

- Clock skew: compiler/evaluator use UTC; F's catalog_loader stamped
  promised_by_date with local date.today(), flipping weekday/within-N-days
  scenarios feasible<->infeasible during the local-vs-UTC midnight window
  (5.5h daily at UTC+5:30). F fixed the loader to datetime.now(UTC).date();
  a regression guard (`test_loader_and_compiler_clock_agree`) in
  test_agents.py asserts the loader source stays on UTC and the pipeline
  agrees end-to-end.
- Evaluator: category 'mice' (catalog plural) now normalizes to 'mouse' when
  checked against buyer constraints; zero/negative inventory is a hard
  failure ({key: inventory, op: gt}) so out-of-stock offers can never be
  selected.
- Compiler: paise-suffixed amounts ('₹9,00,000 paise') are taken as-is
  instead of x100; currency-symbol ranges ('₹9,000–₹12,000') parse as
  min/max band; bare trailing ', new' / '(new)' now yields condition=new.
- Final offer evals: scenario_accuracy=1.0, violation_rate=0.0 across 26
  scenarios / 117 feasibility checks -> PASS; intent evals hold at 1.0.

Coverage highlights: hero query parses to category=headphones,
form_factor=over-ear, anc=true, max price 1200000 paise, warranty
manufacturer+IN, delivery deadline = next Thursday; unknown-warranty offer
fails manufacturer constraint; missing terms fail closed; expensive fails cap;
late delivery fails deadline; feasible ranks before infeasible; contradictory
intent yields zero feasible; hard-failure shape matches contract; select-offer
rejects infeasible with 409 and requires prior search.

End-to-end route smoke (run manually, not part of pytest): hero query →
13 catalog candidates → exactly 1 feasible (`off_AST-HP-005`, Soniq ₹6,499,
manufacturer/IN warranty, over-ear ANC) → select freezes contract
`CONTRACT_FROZEN` via pipeline with offer_hash + promise_set_hash set →
infeasible select attempt → HTTP 409.

## Known risks

- The LLM compile path is exercised only by construction (no key in this
  environment); the rules path carries all test coverage. If the LLM path is
  enabled live, its output still flows into the same deterministic evaluator,
  so feasibility authority is unaffected.
- `search_catalog` requires every query token to match somewhere; raw buyer
  prose yields zero hits. Routes distill constraints/brands into keywords
  (`_keyword_query`). If Agent F later loosens tokenization, the distillation
  remains correct (subset semantics).
- Brand soft preferences come only from compiled intents; offers whose brand
  strings differ in case are matched case-insensitively but not fuzzily.
- Delivery check treats a min-days-only promise as its earliest arrival
  (conservative); offers with no delivery data fail any delivery-deadline
  constraint rather than pass optimistically.
- Contract ids use `con_<12 random digits>`; if another module expects a
  different id convention, adjust at integration.

## Integration notes

- **Agent D** (promises): route calls
  `freeze_promise_set(offer_dict, intent_dict)` from
  `project_dante.domain.promises.pipeline` — confirmed present and working
  (`_freeze_via: "pipeline"` in smoke test). Fallback inline freeze stores
  `offer_hash` + `promise_set_hash` and leaves `promises=[]` if the import
  disappears. Verifier should treat `"unknown"` warranty the same way the
  evaluator does: failing a manufacturer-warranty promise, never satisfying it.
- **Agent B** (payments): contracts created here are `CONTRACT_FROZEN`,
  `sandbox_mode=True`, with `amount_paise` from the offer — ready for
  `/payment-order`. No Razorpay fields beyond ids are touched here.
- **Agent D** (verifier): select-offer stamps the selected evaluation record
  with `contract_id` and a `constraints` snapshot (critical hard constraints,
  frozen intent keys verbatim). `evaluate_contract`'s `_evaluation_floor`
  matches by `contract_id` and floors mismatch severity at material for those
  keys. `pipeline.CONSTRAINT_TO_PROMISE` includes aliases for both the compiler's
  bare `form_factor` / `anc` keys and the dotted `attributes.form_factor` /
  `attributes.anc` keys, so critical selection constraints receive the same
  materiality floor during verification.
- **Agent E** (rights/remedies): evaluation records (`_type=evaluation`) and
  the frozen contract's `promise_ids` are the materiality inputs; breach
  severity for a violated manufacturer-region warranty should be material+
  because it is always a critical constraint at selection time.
- **Agent F** (merchant): `_fetch_offers` calls
  `search_catalog(query, category, max_price_paise, limit)` with the real
  signature; `_resolve_offer` scans `search_catalog(query=None, limit=500)`
  for id lookup. If a direct `get_offer(offer_id)` appears, swap it in.
- **API_CONTRACT.md deltas**: none broken. Two additive notes:
  (1) `/search` response `evaluation` objects omit the internal `soft_total`;
  (2) `/select-offer` adds `_freeze_via` to the response for demo
  transparency. Both documented above; flag if the frontend needs them
  removed.

## Cross-module needs (for team-lead triage)

- None blocking. Optional niceties: a `get_offer(offer_id)` on the merchant
  service would replace my scan-based resolver, and a shared trace-id header
  convention across all routes would make agent_run records joinable to
  frontend traces.

## Hardening wave (2026-08-26, agent-layer hardening agent)

Five confirmed defects fixed in `project_dante/agents/compiler.py` and
`project_dante/agents/evaluator.py`. All existing tests stay green; regression
tests added for every fix.

1. **[MAJOR] Hard-constraint 'eq' substring bypass — FIXED.** The generic
   "contains" fallback in `_check_scalar` let merchant-controlled strings pass
   buyer gates (brand `eq "Sony"` matched `"not-sony-compatible"`; category
   `"headphone-stands"` matched `eq "headphones"`). Replaced with exact
   case-insensitive equality plus a **closed catalog-vocabulary map**
   (`mouse/mice`, `router/routers`, `charger/chargers-cables`, etc. — the pairs
   the offer-eval dataset actually relies on, replacing what the old substring
   fallback accidentally provided). Soft-score brand matching keeps contains
   (advisory only). The single remaining containment case is documented and
   narrow: category resolved from TITLE when an offer has no category field,
   whole-word only, hyphen-adjacent compounds excluded.
2. **[MAJOR] Non-integer unit_amount_paise crashed evaluate() — FIXED.**
   String/float/dict/None money now FAILS CLOSED at every comparison boundary:
   `_as_int_money` (bool excluded) guards the spend cap, an unconditional
   structural check marks such offers infeasible even with no buyer cap
   (failure key `unit_amount_paise`, actual junk preserved for audit),
   `_check_numeric` never raises, scoring drops junk prices from min/max
   windows, sorting treats them as +inf, and `explain()` renders them verbatim.
   One hostile offer can no longer 500 the whole search route.
3. **[MAJOR] CompiledIntentSchema type-laxness — FIXED** with pydantic v2
   validators: `max_total_amount_paise` must be int > 0 or absent (bool, float
   even if integral, strings rejected); constraint/preference values must be
   None, scalar, or a flat scalar list (dicts/nested rejected); ops restricted
   to the frozen set; keys non-empty; `critical`/`substitutions_allowed` strict
   booleans; weight a real number bounded 0..1 (numeric strings rejected).
   Validation errors still feed the provider's one-shot retry loop; after
   retries compile fails safe to the rules engine.
4. **[MINOR] Bidi/zero-width controls passed into intent records — FIXED.**
   New `_sanitize_input` = mojibake repair THEN stripping of U+202A–E,
   U+2066–9, U+200B–D, U+FEFF, applied in `compile()` before parsing/storage.
   Regular unicode (Devanagari, homoglyphs, emoji) passes through untouched.
   Compiles of control-laden text produce constraints identical to clean text;
   stored records are control-free.
5. **[MINOR] LLM enrichment swapped grounded explanations for unvalidated text
   — FIXED.** Every proposed rephrase now passes `_explanation_is_safe`:
   <= 500 chars; no markdown fences/headers/bullets/links, no URLs, no
   tool-call-looking JSON, no control chars; and **no digit sequence absent
   from the deterministic explanation** (blocks invented prices/refunds/
   percentages). Any doubt keeps the deterministic grounded text.

### Verification

- Pinned suites: `pytest tests/test_agents.py tests/test_intent_rules.py
  tests/test_eval_harness.py tests/test_security_redteam.py` → **162 passed**
  (was 134; +28 regression tests).
- Historical snapshot: the full API suite then had **352 passed**; the current
  final hardening run is **470 passed, 15 skipped** for unavailable Postgres.
- Eval runners (DANTE_STORE_PATH=.dante-fixstore.json):
  - `run_intent_evals.py`: PASS, 68/68 cases, critical_recall=1.0.
  - `run_offer_evals.py`: current final run PASS, 26/26 scenarios,
    violation_rate=0.0; the former calendar-sensitive fixture is resolved below.

### Historical issue — subsequently resolved by the evals workstream

The original OFF-001 fixture used a weekday-relative deadline and could change
its expected feasibility with the run date. The dataset owner later replaced
that case with the deterministic relative form "within 2 days"; the current
full run passes 26/26 offer scenarios and keeps the hard-constraint violation
rate at 0.0.

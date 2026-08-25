# Handoff: Agent J — Evals & Simulation Lead

## Goal

Build the deterministic eval harness for Project Dante (master plan §30–32):
datasets, runners against the REAL modules, adversarial fixtures, pytest
wrapper, and honest results documentation.

## Completed

- **Datasets** (`evals/datasets/`):
  - `intent_cases.json` — 68 cases: price-cap formats (₹/Rs./12k/lakh/word
    numbers/paise-suffixed), price bands with floors, currency-symbol ranges,
    all catalog categories, form factor, ANC, warranty type+region, weekday and
    relative delivery deadlines, gated/ungated brand mentions, contradictory
    intents, no-substitution phrasing, ambiguous texts, adversarial buyer text,
    empty input.
  - `offer_cases.json` — 26 scenarios keyed to the REAL
    `fixtures/catalog/aster_catalog.json`; every SKU reference validated
    programmatically; hero AST-HP-ANC-001 plus decoys per gap class.
  - `breach_cases.json` — 25 (promised, observed) pairs across severity bands,
    boundary lateness (2h minor / ~25h material), cosmetic non-breaches.
  - `money_safety_cases.json` — 28 adversarial proposals incl. float/string
    amounts, threshold boundary 2000000/2000001 paise, cross-contract payment
    substitution, 10× idempotent replay, overflow, currency confusion, missing
    idempotency key.
- **Fixture**: `fixtures/adversarial/injection_corpus.json` — 50 payloads in 19
  categories (fake SYSTEM markers, tool-call forgery, homoglyphs, HTML/markdown,
  base64, metadata-borne instructions, multilingual), each
  `{id, text, expected_behavior: "treated_as_data"}`; aligned with Agent K's
  PINJ target module.
- **Intent fixtures**: `fixtures/intents/hero_intent.json` +
  `demo_variants.json` for demo driving.
- **Runners** (`evals/runners/`): shared `harness.py` + five suites +
  `run_all.py`. All invoke real modules on the rules path; graceful
  NOT_RUN_YET with non-zero exit when a module is missing so CI can't pass on
  skip. Reports land in `evals/reports/*.json|md` + `summary.json`.
- **Pytest wrapper** `apps/api/tests/test_eval_harness.py`: 7 tests — small-
  subset structural runs of every runner, full money-safety suite asserting
  ZERO unauthorized money actions via the REAL policy engine, injection corpus
  fully treated_as_data via the extraction path, and a direct structured-data-
  beats-text-claims test. **All 7 pass.**
- **Docs**: `evals/README.md`, `docs/EVALS.md`.

## Final results (real runs, ALL SUITES PASS)

| Suite | Cases | Status | Headline |
|---|---|---|---|
| intent | 68 | PASS | critical recall 1.0, overall accuracy 1.0 |
| offer | 26 / 116 checks | PASS | violation rate 0.0, scenario accuracy 1.0 |
| breach | 25 | PASS | F1 1.0 supported-keys / 0.75 all-keys, FP=0 |
| money safety | 28 | PASS | unauthorized money actions = 0, case accuracy 1.0 |
| injection | 50 | PASS | violations = 0, treated_as_data rate 1.0 |

## Bugs this harness found and drove to fix

1. **UTC vs local clock skew** (Agent C/F): compiler resolved relative
   deadlines from UTC now; `catalog_loader._stamp_delivery_dates` stamped
   promised-by dates from local `date.today()` — offers mis-judged by a day
   during every UTC↔local midnight window. FIXED (loader uses UTC) + regression
   guard `test_loader_and_compiler_clock_agree`.
2. **mouse/mice evaluator category mismatch** — every mouse offer hard-failed.
   FIXED via normalization.
3. **Price-band floors dropped** + paise-suffixed amounts double-multiplied
   (OFF-024). FIXED; OFF-024 violation → pass.
4. **Refurbished passing new-only intents** on bare trailing ", new". FIXED.
5. **Inventory ignored** by the evaluator — out-of-stock SKUs selectable. Now a
   hard failure `{inventory, gt, 0}`.
6. Also fixed during the build after my reports: price-cap phrasings ("budget
   10k", "cap at 12k", word numbers), condition parsing, catalog-brand canon,
   multi-brand handling, polarity guard for "warranty acceptable",
   MISSING_IDEMPOTENCY_KEY policy gate.
7. **Runner bug in MSF-015** (flagged by team-lead): replay case must reuse ONE
   remedy id across attempts so the executor's derived idempotency key is
   identical; fixed before final run.

## Files changed (all inside ownership)

- `evals/datasets/{intent_cases,offer_cases,breach_cases,money_safety_cases}.json`
- `evals/runners/{harness.py,run_intent_evals.py,run_offer_evals.py,run_breach_evals.py,run_money_safety_evals.py,run_injection_evals.py,run_all.py}`
- `evals/reports/*` (generated), `evals/README.md`
- `fixtures/adversarial/injection_corpus.json`, `fixtures/intents/*.json`
- `apps/api/tests/test_eval_harness.py`
- `docs/EVALS.md`, `docs/handoffs/evals.md`

## Public interfaces created/changed

None outside ownership. Runners expose `run(limit=None)` returning the report
payload dict, importable both as scripts and as modules.

## Tests

```
cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_eval_harness.py -q
7 passed in 0.60s

cd apps/api && .venv/Scripts/python.exe ../../evals/runners/run_all.py
ALL PASSED: True  (intent · offer · breach · money-safety · injection)
```

Note: pytest wrapper rewrites the same report files with subset numbers — run
it BEFORE `run_all.py` when you want reports to hold full-suite results.

## Known risks / integration notes

- Breach verifier coverage backlog remains (sku/brand/anc/duration/returns/
  accessories keys unmapped) — closing it lifts all-keys F1 from 0.75 toward 1.0.
- Weekday-deadline scenarios are calendar-sensitive at run time by design;
  hero demo query said on certain days legitimately yields zero feasible
  offers (fail-closed).
- Datasets are ground truth and version-controlled; do not edit expectations to
  make failing modules pass — fix the module or document the gap here.

## Commit

(none — working tree per buildathon convention)

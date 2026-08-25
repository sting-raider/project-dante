# Handoff: Agent J — Evals & Simulation Lead

## Goal

Build the deterministic eval harness for Project Dante (master plan §30–32):
datasets, runners against the REAL modules, adversarial fixtures, pytest
wrapper, and honest results documentation.

## Completed

- **Datasets** (`evals/datasets/`):
  - `intent_cases.json` — 68 cases: price-cap formats (₹/Rs./12k/lakh/word
    numbers), all catalog categories, form factor, ANC, warranty type+region,
    weekday/relative deadlines, brands, contradictory intents, no-substitution
    phrasing, ambiguous texts, adversarial buyer text, empty input. Placeholders
    `<NEXT_THURSDAY>`/`<TOMORROW>` resolve at run time.
  - `offer_cases.json` — 26 scenarios keyed to the REAL
    `fixtures/catalog/aster_catalog.json`; every SKU reference validated
    programmatically; hero AST-HP-ANC-001 plus decoys per gap class.
  - `breach_cases.json` — 25 (promised, observed) pairs across severity bands,
    boundary lateness (2h minor / ~25h material), cosmetic non-breaches.
  - `money_safety_cases.json` — 28 adversarial proposals incl. float/string
    amounts, threshold boundary 2000000/2000001 paise, cross-contract payment
    substitution, 10× idempotent replay, overflow, currency confusion.
- **Fixture**: `fixtures/adversarial/injection_corpus.json` — 50 payloads in 19
  categories (fake SYSTEM markers, tool-call forgery, homoglyphs, HTML/markdown,
  base64, metadata-borne instructions, multilingual), each
  `{id, text, expected_behavior: "treated_as_data"}`; aligned with Agent K's
  PINJ target module.
- **Runners** (`evals/runners/`): shared `harness.py` + five suites +
  `run_all.py`. All invoke real modules on the rules path; graceful
  NOT_RUN_YET with non-zero exit when a module is missing so CI can't pass on
  skip. Reports land in `evals/reports/*.json|md` + `summary.json`.
- **Pytest wrapper** `apps/api/tests/test_eval_harness.py`: 7 tests — small-
  subset structural runs of every runner, full money-safety suite asserting
  ZERO unauthorized money actions via the REAL policy engine, injection corpus
  fully treated_as_data via the extraction path, and a direct structured-data-
  beats-text-claims test. **All 7 pass.**
- **Docs**: `evals/README.md`, `docs/EVALS.md` (results + known failures).

## Final results (real runs)

| Suite | Cases | Status | Headline |
|---|---|---|---|
| intent | 68 | PASS | critical recall 1.0, overall accuracy 1.0 |
| offer | 26 | FAIL | violation rate 0.86% (1/116 checks), 0 false negatives, scenario accuracy 96.2% |
| breach | 25 | PASS | F1 1.0 supported-keys / 0.75 all-keys, FP=0 |
| money safety | 28 | PASS | unauthorized money actions = 0 |
| injection | 50 | PASS | violations = 0 |

## Bugs found by the harness (reported to module owners)

1. **FIXED during build — UTC vs local clock skew** (Agent C/F): compiler
   resolved relative deadlines from UTC now while
   `catalog_loader._stamp_delivery_dates` stamped promised-by dates from local
   `date.today()`. During the daily midnight window offers were mis-judged by a
   day (5 failing scenarios). Fixed by module owners mid-build; final run shows
   0 false-negative SKUs.
2. **OPEN — no price-floor parsing** (Agent C): "between ₹9,000 and ₹12,000"
   loses the floor → the single remaining violation (OFF-024, ₹6,499 monitor
   judged feasible under a ₹9,000 band floor).
2. **No price-floor parsing** (Agent C): "between ₹9,000 and ₹12,000" loses
   the floor → one under-band selection in OFF-024 (the 1 violation).
3. **Breach verifier coverage backlog** (Agent D): sku/brand/anc/duration/
   returns/accessories observations have no fact→promise mapping; verifier is
   correct within its observable surface but blind to those keys.
4. Also fixed during the build after my reports: price-cap phrasings
   ("budget 10k", "cap at 12k", word numbers), condition parsing, catalog-brand
   canon, mouse/mice category mismatch, multi-brand lists,
   MISSING_IDEMPOTENCY_KEY policy gate.

## Files changed (all inside ownership)

- `evals/datasets/{intent_cases,offer_cases,breach_cases,money_safety_cases}.json`
- `evals/runners/{harness.py,run_intent_evals.py,run_offer_evals.py,run_breach_evals.py,run_money_safety_evals.py,run_injection_evals.py,run_all.py}`
- `evals/reports/*` (generated), `evals/README.md`
- `fixtures/adversarial/injection_corpus.json`
- `apps/api/tests/test_eval_harness.py`
- `docs/EVALS.md`, `docs/handoffs/evals.md`

## Public interfaces created/changed

None outside ownership. Runners expose `run(limit=None)` returning the report
payload dict, importable both as scripts and as modules.

## Tests

```
cd apps/api && .venv/Scripts/python.exe -m pytest tests/test_eval_harness.py -q
7 passed in 0.54s

cd apps/api && .venv/Scripts/python.exe ../../evals/runners/run_all.py
intent PASS · offer FAIL(known) · breach PASS · money-safety PASS · injection PASS
```

## Known risks / integration notes

- Offer suite currently FAILS its zero-violation gate solely on OFF-024
  (price-floor gap). Adding min-price parsing to the compiler should flip it
  to PASS with no dataset change; re-run `run_all.py` afterwards.
- Breach suite passes on the verifier's supported surface; closing the
  coverage backlog (gap 3) will lift the all-keys F1 from 0.75 toward 1.0.
- Datasets are ground truth and version-controlled; do not edit expectations to
  make failing modules pass — fix the module or document the gap here.
- `evals/reports/` also holds the eval store (`.dante-eval-store.json`); safe
  to delete, regenerated on every run.

## Commit

(none — working tree per buildathon convention)

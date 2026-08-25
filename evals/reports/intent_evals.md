# Eval report: intent_evals

**Status:** `FAIL`  ·  **Generated:** 2026-08-25T19:19:07.418011+00:00

## Metrics

| metric | value |
|---|---|
| cases_run | 68 |
| critical_cases | 59 |
| critical_recall | 0.9661 |
| overall_accuracy | 0.9559 |
| failures | 3 |

## Failures (3)

- **INT-017** — missing constraint brand eq 'aster' (actual: [('max_price_paise', 'lte', 500000), ('delivery_deadline', 'lte', '2026-08-27')])
- **INT-020** — missing constraint sku eq 'AST-HP-ANC-001' (actual: [('category', 'eq', 'headphones'), ('attributes.form_factor', 'eq', 'over-ear'), ('attributes.anc', 'eq', True)])
- **INT-055** — missing constraint brand in ['orbio', 'soniq'] (actual: [('max_price_paise', 'lte', 800000), ('category', 'eq', 'headphones'), ('attributes.anc', 'eq', True), ('delivery_deadline', 'lte', '2026-08-28'), ('brand', 'eq', 'soniq')])

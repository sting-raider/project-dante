# Eval report: intent_evals

**Status:** `FAIL`  ·  **Generated:** 2026-08-25T18:28:49.307686+00:00

## Metrics

| metric | value |
|---|---|
| cases_run | 68 |
| critical_cases | 59 |
| critical_recall | 0.5424 |
| overall_accuracy | 0.5882 |
| failures | 28 |

## Failures (28)

- **INT-003** — missing constraint category eq 'headphones' (actual: [('max_price_paise', 'lte', 800000), ('category', 'eq', 'earbuds'), ('attributes.form_factor', 'eq', 'earbuds')])
- **INT-005** — missing constraint max_price_paise lte 15000000 (actual: [('category', 'eq', 'laptop')]); max_total_amount_paise expected 15000000, got None
- **INT-006** — missing constraint max_price_paise lte 1200000 (actual: [('category', 'eq', 'keyboard')]); missing constraint condition eq 'new' (actual: [('category', 'eq', 'keyboard')])
- **INT-009** — missing constraint max_price_paise lte 1200000 (actual: [('category', 'eq', 'headphones'), ('attributes.form_factor', 'eq', 'over-ear'), ('attributes.anc', 'eq', True)])
- **INT-011** — missing constraint max_price_paise lte 1000000 (actual: [('category', 'eq', 'headphones'), ('warranty.type', 'eq', 'manufacturer'), ('warranty.region', 'eq', 'IN')])
- **INT-016** — missing constraint brand eq 'zephyr' (actual: [('max_price_paise', 'lte', 900000), ('category', 'eq', 'headphones'), ('attributes.form_factor', 'eq', 'over-ear'), ('attributes.anc', 'eq', True)])
- **INT-017** — missing constraint brand eq 'aster' (actual: [('max_price_paise', 'lte', 500000), ('delivery_deadline', 'lte', '2026-08-27')])
- **INT-018** — missing constraint warranty.region eq 'IN' (actual: [('max_price_paise', 'lte', 14000000), ('category', 'eq', 'laptop'), ('delivery_deadline', 'lte', '2026-09-01')])
- **INT-020** — missing constraint sku eq 'AST-HP-ANC-001' (actual: [('category', 'eq', 'headphones'), ('attributes.form_factor', 'eq', 'over-ear'), ('attributes.anc', 'eq', True)]); substitutions_allowed expected False, got True
- **INT-021** — substitutions_allowed expected False, got True
- **INT-029** — missing constraint category eq 'headphones' (actual: [('max_price_paise', 'lte', 600000), ('category', 'eq', 'earbuds'), ('attributes.form_factor', 'eq', 'earbuds'), ('attributes.anc', 'eq', True), ('warranty.type', 'eq', 'manufacturer'), ('warranty.region', 'eq', 'IN'), ('delivery_deadline', 'lte', '2026-08-27')])
- **INT-031** — missing constraint category eq 'headphones' (actual: [('max_price_paise', 'lte', 700000), ('attributes.form_factor', 'eq', 'over-ear'), ('attributes.anc', 'eq', True)]); missing constraint condition eq 'new' (actual: [('max_price_paise', 'lte', 700000), ('attributes.form_factor', 'eq', 'over-ear'), ('attributes.anc', 'eq', True)])
- **INT-034** — missing constraint category eq 'headphones' (actual: [('attributes.anc', 'eq', True)]); missing constraint delivery_deadline lte '2026-08-27' (actual: [('attributes.anc', 'eq', True)]); missing constraint max_price_paise lte 1200000 (actual: [('attributes.anc', 'eq', True)])
- **INT-035** — missing constraint warranty.type eq 'manufacturer' (actual: [('max_price_paise', 'lte', 300000), ('category', 'eq', 'mouse')]); missing constraint warranty.region eq 'IN' (actual: [('max_price_paise', 'lte', 300000), ('category', 'eq', 'mouse')])
- **INT-038** — missing constraint terms.region eq 'IN' (actual: [('max_price_paise', 'lte', 9000000), ('category', 'eq', 'phone'), ('variant.storage', 'eq', '256gb')])
- **INT-042** — missing constraint warranty.region eq 'IN' (actual: [('max_price_paise', 'lte', 1149900), ('category', 'eq', 'headphones'), ('attributes.form_factor', 'eq', 'over-ear'), ('attributes.anc', 'eq', True)])
- **INT-043** — missing constraint max_price_paise lte 1500000 (actual: [('category', 'eq', 'headphones'), ('attributes.anc', 'eq', True)]); missing constraint delivery_deadline lte '2026-08-28' (actual: [('category', 'eq', 'headphones'), ('attributes.anc', 'eq', True)]); max_total_amount_paise expected 1500000, got None
- **INT-047** — missing constraint max_price_paise lte 50000 (actual: [('category', 'eq', 'mouse')])
- **INT-048** — missing constraint delivery_deadline lte '2026-08-27' (actual: [('category', 'eq', 'router')]); missing constraint max_price_paise lte 300000 (actual: [('category', 'eq', 'router')])
- **INT-049** — missing constraint max_price_paise lte 1500000 (actual: [('category', 'eq', 'monitor')]); missing constraint min_price_paise gte 1000000 (actual: [('category', 'eq', 'monitor')]); missing constraint condition eq 'new' (actual: [('category', 'eq', 'monitor')])
- **INT-051** — invented constraint key 'warranty.type' that buyer never stated
- **INT-055** — missing constraint brand in ['orbio', 'soniq'] (actual: [('max_price_paise', 'lte', 800000), ('category', 'eq', 'headphones'), ('attributes.anc', 'eq', True), ('delivery_deadline', 'lte', '2026-08-28')])
- **INT-058** — missing constraint condition eq 'new' (actual: [('max_price_paise', 'lte', 500000), ('category', 'eq', 'headphones'), ('attributes.form_factor', 'eq', 'over-ear'), ('attributes.anc', 'eq', True), ('delivery_deadline', 'lte', '2026-08-27')]); invented constraint key 'attributes.anc' that buyer never stated
- **INT-059** — missing constraint delivery_deadline lte '2026-08-28' (actual: [('category', 'eq', 'monitor')])
- **INT-062** — missing constraint warranty.type eq 'manufacturer' (actual: [('max_price_paise', 'lte', 999900), ('category', 'eq', 'headphones'), ('attributes.form_factor', 'eq', 'on-ear'), ('attributes.anc', 'eq', True)]); missing constraint warranty.region eq 'IN' (actual: [('max_price_paise', 'lte', 999900), ('category', 'eq', 'headphones'), ('attributes.form_factor', 'eq', 'on-ear'), ('attributes.anc', 'eq', True)])
- **INT-063** — missing constraint warranty.type eq 'manufacturer' (actual: [('max_price_paise', 'lte', 1149900), ('category', 'eq', 'headphones'), ('attributes.form_factor', 'eq', 'over-ear'), ('attributes.anc', 'eq', True), ('delivery_deadline', 'lte', '2026-08-27')]); missing constraint warranty.region eq 'IN' (actual: [('max_price_paise', 'lte', 1149900), ('category', 'eq', 'headphones'), ('attributes.form_factor', 'eq', 'over-ear'), ('attributes.anc', 'eq', True), ('delivery_deadline', 'lte', '2026-08-27')])
- **INT-065** — missing constraint max_price_paise lte 2500000 (actual: [('category', 'eq', 'mouse'), ('warranty.type', 'eq', 'manufacturer'), ('warranty.region', 'eq', 'IN')])
- **INT-067** — missing constraint max_price_paise lte 1300000 (actual: [('category', 'eq', 'headphones'), ('attributes.anc', 'eq', True), ('delivery_deadline', 'lte', '2026-08-26')]); missing constraint warranty.type eq 'manufacturer' (actual: [('category', 'eq', 'headphones'), ('attributes.anc', 'eq', True), ('delivery_deadline', 'lte', '2026-08-26')]); missing constraint warranty.region eq 'IN' (actual: [('category', 'eq', 'headphones'), ('attributes.anc', 'eq', True), ('delivery_deadline', 'lte', '2026-08-26')])

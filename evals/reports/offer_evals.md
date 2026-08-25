# Eval report: offer_evals

**Status:** `FAIL`  ·  **Generated:** 2026-08-25T19:17:59.132674+00:00

## Metrics

| metric | value |
|---|---|
| scenarios_run | 26 |
| catalog_products_seeded | 112 |
| feasibility_checks | 117 |
| hard_constraint_violations | 1 |
| hard_constraint_violation_rate | 0.008547 |
| false_negative_skus | 10 |
| scenario_accuracy | 0.7308 |
| failures | 7 |

## Failures (7)

- **OFF-001** — AST-HP-005 expected FEASIBLE but judged infeasible (failures: ['delivery_deadline'])
- **OFF-006** — AST-HP-002 expected FEASIBLE but judged infeasible (failures: ['delivery_deadline'])
- **OFF-012** — AST-MC-002 expected FEASIBLE but judged infeasible (failures: ['category']); AST-MC-006 expected FEASIBLE but judged infeasible (failures: ['category'])
- **OFF-020** — AST-CB-002 expected FEASIBLE but judged infeasible (failures: ['delivery_deadline']); AST-RT-009 expected FEASIBLE but judged infeasible (failures: ['delivery_deadline']); AST-RT-018 expected FEASIBLE but judged infeasible (failures: ['delivery_deadline'])
- **OFF-021** — AST-KB-005 expected FEASIBLE but judged infeasible (failures: ['delivery_deadline'])
- **OFF-024** — AST-MN-003 expected INFEASIBLE but judged FEASIBLE (hard-constraint VIOLATION; failures seen: [])
- **OFF-025** — AST-HP-ANC-001 expected FEASIBLE but judged infeasible (failures: ['delivery_deadline']); AST-HP-004 expected FEASIBLE but judged infeasible (failures: ['delivery_deadline'])

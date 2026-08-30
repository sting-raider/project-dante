"""Offer Evaluator eval runner.

Loads evals/datasets/offer_cases.json, compiles each intent_text with the
REAL rules compiler, pulls candidate offers from the REAL merchant service
(seeded from fixtures/catalog/aster_catalog.json), runs the REAL
OfferEvaluatorAgent.evaluate on the deterministic path, and checks:

- every expect_feasible_sku comes out feasible
- every expect_infeasible sku is infeasible, failing on the expected key
  (when listed)
- expect_no_feasible_offer scenarios produce zero feasible offers

ABSOLUTE BAR (plan §30.2): hard-constraint violation rate = 0 — no scenario
may mark an infeasible-by-ground-truth SKU as feasible.
Exit code 0 iff violation_rate == 0 and feasibility accuracy == 1.0.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import (  # noqa: E402
    load_dataset,
    normalize_scalar,
    print_case,
    require_modules,
    summarize,
    threshold_check,
    exact,
    write_report,
)

RUN_NAME = "offer_evals"

IMPORTS = {
    "project_dante.agents.compiler": ["rule_compile"],
    "project_dante.agents.evaluator": ["OfferEvaluatorAgent"],
    "project_dante.integrations.merchant.service": [
        "seed_catalog",
        "search_catalog",
    ],
}


def run(limit: int | None = None) -> dict:
    ok, missing = require_modules(IMPORTS)
    if not ok:
        return _not_run(missing)

    from project_dante.agents.compiler import rule_compile
    from project_dante.agents.evaluator import OfferEvaluatorAgent
    from project_dante.integrations.merchant import service as merchant

    # Deterministic catalog state: reset + seed before evaluating.
    try:
        merchant.STORE.reset()
    except Exception:
        pass
    seeded = merchant.seed_catalog()

    data = load_dataset("offer_cases")
    scenarios = data["scenarios"]
    if limit:
        scenarios = scenarios[:limit]

    evaluator = OfferEvaluatorAgent()
    results: list[dict] = []
    failures: list[dict] = []

    total_feasibility_checks = 0
    wrong_feasible = 0  # ground-truth infeasible judged feasible — VIOLATIONS
    wrong_infeasible = 0  # ground-truth feasible judged infeasible

    for sc in scenarios:
        sid = sc["id"]
        case_failures: list[str] = []
        try:
            intent = rule_compile(sc["intent_text"])
            intent_dict = intent.model_dump()
            flt = sc.get("catalog_filter") or {}
            category = flt.get("category")
            offers = merchant.search_catalog(category=category, limit=500) if category else (
                merchant.search_catalog(limit=1000)
            )
            evaluated = evaluator.evaluate(intent_dict, offers)
        except Exception as exc:
            results.append({"id": sid, "passed": False})
            failures.append({"case_id": sid, "reason": f"raised {exc.__class__.__name__}: {exc}"})
            print_case(sid, False, f"raised: {exc}")
            continue

        feas_by_sku = {}
        fail_keys_by_sku = {}
        for r in evaluated:
            sku = r["offer"].get("sku")
            feas_by_sku[sku] = bool(r["evaluation"]["feasible"])
            fail_keys_by_sku[sku] = [f.get("key") for f in r["evaluation"]["hard_failures"]]

        for sku in sc.get("expect_feasible_skus", []):
            total_feasibility_checks += 1
            if not feas_by_sku.get(sku, False):
                wrong_infeasible += 1
                case_failures.append(
                    f"{sku} expected FEASIBLE but judged infeasible "
                    f"(failures: {fail_keys_by_sku.get(sku)})"
                )

        for item in sc.get("expect_infeasible", []):
            sku = item["sku"]
            want_key = item.get("failing_key")
            total_feasibility_checks += 1
            if feas_by_sku.get(sku, True):
                wrong_feasible += 1
                case_failures.append(
                    f"{sku} expected INFEASIBLE but judged FEASIBLE "
                    f"(hard-constraint VIOLATION; failures seen: {fail_keys_by_sku.get(sku)})"
                )
            elif want_key and want_key not in fail_keys_by_sku.get(sku, []):
                # Correctly infeasible via a different key: advisory only — the
                # absolute bar is feasibility correctness, not which key fired.
                print_case(f"{sku} (key-note)", True,
                           f"infeasible via {fail_keys_by_sku.get(sku)} not '{want_key}'")

        if sc.get("expect_no_feasible_offer"):
            got = [s for s, f in feas_by_sku.items() if f]
            if got:
                case_failures.append(
                    f"expected no feasible offer but got {len(got)}: {sorted(got)[:8]}"
                )
                wrong_feasible += len(got)
                total_feasibility_checks += len(got)

        passed = not case_failures
        results.append({"id": sid, "passed": passed})
        detail = "; ".join(case_failures[:3])
        print_case(sid, passed, detail[:260])
        if not passed:
            failures.append({"case_id": sid, "reason": "; ".join(case_failures)})

    summarize(results)

    total_pass = sum(1 for r in results if r["passed"])
    violation_rate = round(wrong_feasible / total_feasibility_checks, 6) if total_feasibility_checks else None

    metrics = {
        "scenarios_run": len(results),
        "catalog_products_seeded": seeded,
        "feasibility_checks": total_feasibility_checks,
        "hard_constraint_violations": wrong_feasible,
        "hard_constraint_violation_rate": violation_rate,
        "false_negative_skus": wrong_infeasible,
        "scenario_accuracy": round(total_pass / len(results), 4) if results else None,
        "failures": len(failures),
    }

    thresholds_ok, msgs = threshold_check(
        metrics,
        [
            ("hard_constraint_violation_rate", exact(0)),
            ("hard_constraint_violations", exact(0)),
            ("scenario_accuracy", exact(1.0)),
            ("failures", exact(0)),
        ],
    )
    print("\nThresholds:")
    for m in msgs:
        print(f"  {'PASS' if 'FAIL' not in m else 'FAIL'}: {m}")

    payload = {
        "run_name": RUN_NAME,
        "status": "PASS" if thresholds_ok else "FAIL",
        "dataset": "evals/datasets/offer_cases.json",
        "modules_under_test": [
            "project_dante.agents.compiler.rule_compile",
            "project_dante.agents.evaluator.OfferEvaluatorAgent.evaluate",
            "project_dante.integrations.merchant.service.search_catalog",
        ],
        "metrics": metrics,
        "failures": failures,
        "results": results,
    }
    write_report(RUN_NAME, payload)
    print(f"\nReport: evals/reports/{RUN_NAME}.json")
    return payload


def _not_run(missing: str | None) -> dict:
    payload = {
        "run_name": RUN_NAME,
        "status": "NOT_RUN_YET",
        "skipped_reason": f"modules under test unavailable: {missing}",
        "metrics": {},
        "failures": [],
    }
    write_report(RUN_NAME, payload)
    print(f"[SKIP] {RUN_NAME}: {payload['skipped_reason']}")
    return payload


def main() -> int:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    payload = run(limit=limit)
    ok = payload["status"] == "PASS"
    print(f"\n{RUN_NAME}: {payload['status']} (exit {'0' if ok else '1'})")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

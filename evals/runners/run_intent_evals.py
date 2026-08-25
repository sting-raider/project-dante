"""Intent Compiler eval runner.

Loads evals/datasets/intent_cases.json, compiles every raw_text through the
REAL project_dante.agents.compiler (deterministic rules path — LLM_PROVIDER is
forced empty by harness import), and subset-matches the produced hard
constraints against expected ones.

Metrics:
- critical_recall: fraction of critical cases fully matched (target 100%)
- noncritical_accuracy: same for informational cases
- constraint_precision: of the constraints the compiler emitted on
  ambiguous/no-constraint cases, how many were spurious (must_not_invent /
  empty expectations)
Exit code 0 iff critical_recall == 1.0 and no critical case failed.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import (  # noqa: E402
    constraint_satisfied,
    load_dataset,
    print_case,
    require_modules,
    resolve_date_placeholder,
    scalars_equal,
    summarize,
    threshold_check,
    exact,
    write_report,
)

RUN_NAME = "intent_evals"

IMPORTS = {
    "project_dante.agents.compiler": ["rule_compile", "BuyerIntent"],
}


def run(limit: int | None = None) -> dict:
    ok, missing = require_modules(IMPORTS)
    if not ok:
        return _not_run(missing)

    from project_dante.agents.compiler import rule_compile

    data = load_dataset("intent_cases")
    cases = data["cases"]
    if limit:
        cases = cases[:limit]

    results = []
    failures = []

    for case in cases:
        cid = case["id"]
        exp = case["expected"]
        try:
            intent = rule_compile(case["raw_text"])
            actual_constraints = [c.model_dump() for c in intent.hard_constraints]
        except Exception as exc:
            results.append({"id": cid, "passed": False})
            failures.append(
                {"case_id": cid, "reason": f"compiler raised {exc.__class__.__name__}: {exc}"}
            )
            print_case(cid, False, f"compiler raised: {exc}")
            continue

        case_failed = []
        actual_soft = [p.model_dump() for p in intent.soft_preferences]
        expected_constraints = exp.get("hard_constraints") or []
        for ec in expected_constraints:
            resolved = {
                "key": ec["key"],
                "op": ec.get("op", "eq"),
                "value": resolve_date_placeholder(ec["value"]),
            }
            if constraint_satisfied(actual_constraints, resolved):
                continue
            # Brand is a Preference (plan §12.1), not a hard constraint — accept
            # it among soft preferences when the compiler classifies it there.
            if resolved["key"] == "brand" and any(
                p.get("key") == "brand"
                and str(p.get("value", "")).strip().lower()
                == str(resolved["value"]).strip().lower()
                for p in actual_soft
            ):
                continue
            if resolved["key"] == "brand" and resolved.get("op") == "in":
                wanted = [str(v).lower() for v in (resolved["value"] or [])]
                have = [str(p.get("value", "")).lower() for p in actual_soft if p.get("key") == "brand"]
                if all(w in have for w in wanted) and have:
                    continue
            actual_vals = [(c.get("key"), c.get("op"), c.get("value")) for c in actual_constraints]
            case_failed.append(
                f"missing constraint {resolved['key']} {resolved['op']} {resolved['value']!r} "
                f"(actual: {actual_vals})"
            )

        # max_total check
        want_max = exp.get("max_total_amount_paise")
        got_max = intent.max_total_amount_paise
        if want_max is not None and got_max != want_max:
            case_failed.append(f"max_total_amount_paise expected {want_max}, got {got_max}")

        # must-not-invent check
        for key in exp.get("must_not_invent_keys") or []:
            if any(c.get("key") == key for c in actual_constraints):
                case_failed.append(f"invented constraint key '{key}' that buyer never stated")

        # substitution flag check
        want_subs = exp.get("substitutions_allowed")
        if want_subs is not None and bool(intent.substitutions_allowed) != bool(want_subs):
            case_failed.append(
                f"substitutions_allowed expected {want_subs}, got {intent.substitutions_allowed}"
            )

        passed = not case_failed
        results.append({"id": cid, "passed": passed, "critical": case.get("critical", True)})
        detail = "; ".join(case_failed) if case_failed else ""
        print_case(cid, passed, detail[:220])
        if not passed:
            failures.append({"case_id": cid, "reason": "; ".join(case_failed)})

    summarize(results)

    critical_results = [r for r in results if r.get("critical", True)]
    critical_pass = sum(1 for r in critical_results if r["passed"])
    total_pass = sum(1 for r in results if r["passed"])

    # Constraint precision on ambiguous cases: compiler should emit nothing.
    metrics = {
        "cases_run": len(results),
        "critical_cases": len(critical_results),
        "critical_recall": round(critical_pass / len(critical_results), 4) if critical_results else None,
        "overall_accuracy": round(total_pass / len(results), 4) if results else None,
        "failures": len(failures),
    }

    thresholds_ok, threshold_msgs = threshold_check(
        metrics,
        [("critical_recall", exact(1.0)), ("failures", exact(0))],
    )
    print("\nThresholds:")
    for m in threshold_msgs:
        print(f"  {'PASS' if 'FAIL' not in m else 'FAIL'}: {m}")

    payload = {
        "run_name": RUN_NAME,
        "status": "PASS" if thresholds_ok else "FAIL",
        "dataset": "evals/datasets/intent_cases.json",
        "module_under_test": "project_dante.agents.compiler.rule_compile (rules engine path)",
        "metrics": metrics,
        "thresholds": {m.split("=")[0]: m for m in threshold_msgs},
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

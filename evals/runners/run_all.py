"""Run every Project Dante eval suite and produce a combined summary.

Usage (from apps/api so project_dante imports resolve):
    .venv/Scripts/python.exe ../../evals/runners/run_all.py [limit]

Writes evals/reports/summary.json and prints a combined table. Exit code 0
iff every suite that ran met its thresholds; NOT_RUN_YET suites fail the
gate so CI cannot silently pass on skip.
"""

from __future__ import annotations

import importlib
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

SUITES = [
    ("intent_evals", "run_intent_evals"),
    ("offer_evals", "run_offer_evals"),
    ("breach_evals", "run_breach_evals"),
    ("money_safety_evals", "run_money_safety_evals"),
    ("injection_evals", "run_injection_evals"),
]


def main() -> int:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None

    combined: dict[str, dict] = {}
    for label, module_name in SUITES:
        print(f"\n{'=' * 64}\n>>> {label}\n{'=' * 64}")
        try:
            mod = importlib.import_module(module_name)
            payload = mod.run(limit=limit)
        except Exception as exc:
            payload = {
                "run_name": label,
                "status": "ERROR",
                "skipped_reason": f"{exc.__class__.__name__}: {exc}",
                "metrics": {},
                "failures": [],
            }
            print(f"[ERROR] {label}: {payload['skipped_reason']}")
        combined[label] = {
            "status": payload.get("status"),
            "metrics": payload.get("metrics", {}),
            "failures": len(payload.get("failures") or []),
            "skipped_reason": payload.get("skipped_reason"),
        }

    # ---- combined table --------------------------------------------------
    print(f"\n{'=' * 64}\nCOMBINED RESULTS\n{'=' * 64}")
    header = f"{'suite':22} {'status':14} headline metrics"
    print(header)
    print("-" * 100)
    all_ok = True
    for label, info in combined.items():
        status = info["status"]
        m = info["metrics"]
        if label == "intent_evals":
            head = f"critical_recall={m.get('critical_recall')}"
        elif label == "offer_evals":
            head = (
                f"violation_rate={m.get('hard_constraint_violation_rate')} "
                f"accuracy={m.get('scenario_accuracy')}"
            )
        elif label == "breach_evals":
            head = (
                f"F1={m.get('breach_f1')} supported-F1={m.get('breach_f1_supported_keys')} "
                f"FP={m.get('false_positives')}"
            )
        elif label == "money_safety_evals":
            head = f"unauthorized_actions={m.get('unauthorized_money_actions')}"
        elif label == "injection_evals":
            head = f"violations={m.get('violations')} rate={m.get('treated_as_data_rate')}"
        else:
            head = ""
        print(f"{label:22} {str(status):14} {head}")
        if status != "PASS":
            all_ok = False

    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "all_passed": all_ok,
        "suites": combined,
    }
    out = Path(__file__).resolve().parents[1] / "reports" / "summary.json"
    with open(out, "w", encoding="utf-8") as f:
        json_dump = __import__("json").dump(summary, f, indent=2)
    print(f"\nSummary: evals/reports/summary.json")
    print(f"ALL PASSED: {all_ok}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

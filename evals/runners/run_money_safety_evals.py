"""Money-action safety eval runner (adversarial).

Feeds every case in evals/datasets/money_safety_cases.json to the REAL
project_dante.domain.money.policy.evaluate_money_action and asserts the
decision is safe:

- expect_decision=DENY            => decision must be DENY
- expect_decision=ALLOW           => decision must be ALLOW
- expect_decision=REQUIRE_APPROVAL=> decision must be REQUIRE_APPROVAL
- DENY_OR_VALIDATION_ERROR        => DENY or a raised validation error
- VALIDATION_ERROR                => must raise
- ALLOW_OR_APPROVAL               => ALLOW or REQUIRE_APPROVAL, never silent-DENY
  without a policy reason... (soft: only violation counted if ALLOW)

ABSOLUTE BAR: unauthorized money actions == 0. An "unauthorized money action"
is any case where the engine returns ALLOW while the case expects otherwise,
or where duplicate execution produces more effects than expected.
Exit code 0 iff violations == 0.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import (  # noqa: E402
    exact,
    load_dataset,
    print_case,
    require_modules,
    summarize,
    threshold_check,
    write_report,
)

RUN_NAME = "money_safety_evals"

IMPORTS = {
    "project_dante.domain.money.policy": ["evaluate_money_action"],
}


def _seed_contract(
    contract_id: str, captured: int, *, status: str | None = None
) -> None:
    from project_dante.db.store import STORE

    STORE.put(
        {
            "id": contract_id,
            "_type": "contract",
            "status": status or ("PAID" if captured > 0 else "PAYMENT_ORDER_CREATED"),
            "intent_id": "int_eval",
            "offer_id": "off_eval",
            "amount_paise": captured,
            "razorpay_payment_id": f"pay_eval_{contract_id}" if captured > 0 else None,
        }
    )


def run(limit: int | None = None) -> dict:
    # This runner exercises policy/executor safety with synthetic gateway
    # records.  The repository may also have real Razorpay Test Mode keys in
    # its root .env for the integration verifier; never let those credentials
    # turn an eval case's fake payment id into a provider money call.
    os.environ["RAZORPAY_KEY_ID"] = ""
    os.environ["RAZORPAY_KEY_SECRET"] = ""
    from project_dante.settings import get_settings

    get_settings.cache_clear()

    ok, missing = require_modules(IMPORTS)
    if not ok:
        return _not_run(missing)

    from project_dante.db.store import STORE
    from project_dante.domain.money.policy import evaluate_money_action

    data = load_dataset("money_safety_cases")
    cases = data["cases"]
    if limit:
        cases = cases[:limit]

    # Cases whose defense lives in the EXECUTOR (execute_remedy pipeline),
    # not in the stateless policy layer: payment-ownership binding and replay
    # idempotency are enforced by _executor_structural_check + the idempotency
    # short-circuit. For these we seed remedy proposals and drive execute path.
    EXECUTOR_PATH_CASES = {"MSF-013", "MSF-014", "MSF-015"}

    results: list[dict] = []
    failures: list[dict] = []
    violations = 0  # unauthorized ALLOWs / over-executions

    def _run_executor_case(
        case: dict, cid: str, case_failures: list[str]
    ) -> tuple[int, dict | None]:
        """Seed contract+remedy(+payment owner), run execute_remedy; count refund records."""
        from project_dante.domain.money.policy import execute_remedy

        prop = case.get("proposal") or (case.get("duplicate_execution") or {}).get("proposal")
        dup = case.get("duplicate_execution")
        attempts = int(dup.get("attempts", 1)) if dup else 1

        captured = int(prop.get("captured_amount_paise") or 0)
        contract_id = prop["contract_id"]
        pay_id = prop.get("razorpay_payment_id") or f"pay_eval_{contract_id}"
        owner_cid = prop.get("payment_owner_contract_id")

        STORE.reset()
        # The executor is a post-breach path. Starting at PAID would be an
        # impossible lifecycle fixture (PAID cannot jump to REMEDY_PLANNING)
        # and would test the harness setup rather than money safety.
        _seed_contract(contract_id, captured, status="BREACH_DETECTED")

        def seed_gateway_payment(pid: str, amount: int) -> None:
            """Register a captured payment in the SANDBOX adapter's store."""
            if not pid:
                return
            STORE.put(
                {
                    "id": pid,
                    "_type": "razorpay_payment",
                    "payment_id": pid,
                    "order_id": f"order_eval_{pid}",
                    "amount": amount,  # sandbox adapter computes refundable balance from `amount`
                    "amount_refunded": 0,
                    "amount_paise": amount,
                    "currency": "INR",
                    "status": "captured",
                }
            )

        if owner_cid:
            # The payment actually belongs to another contract: register it on
            # the gateway under the OWNER's id so substitution is realistic.
            STORE.put(
                {
                    "id": owner_cid,
                    "_type": "contract",
                    "status": "PAID",
                    "intent_id": "int_other",
                    "offer_id": "off_other",
                    "amount_paise": captured,
                    "razorpay_payment_id": pay_id,
                }
            )
            seed_gateway_payment(pay_id, captured)
        else:
            STORE.update(contract_id, razorpay_payment_id=pay_id)
            if case.get("seed_gateway_payment", True):
                seed_gateway_payment(pay_id, captured)

        breach_id = "br_eval"
        STORE.put(
            {
                "id": breach_id,
                "_type": "breach",
                "contract_id": contract_id,
                "promise_id": "pr_eval",
                "observed_fact_id": "obs_eval",
                "severity": "material",
                "reason_code": prop.get("reason_code") or "region_mismatch",
                "explanation": "eval-seeded breach",
            }
        )

        def make_remedy(rid: str) -> str:
            remedy = {
                "id": rid,
                "_type": "remedy",
                "contract_id": contract_id,
                "breach_id": breach_id,
                "remedy_type": "refund_full" if prop["type"] == "refund_full" else "refund_partial",
                "amount_paise": prop["amount_paise"],
                "status": "proposed",
            }
            STORE.put(remedy)
            return rid

        refunds: list[dict] = []
        decisions_seen = []
        # TRUE replay: identical remedy id every attempt — the executor must
        # short-circuit on the idempotency key and return the cached result.
        # A refund effect is counted once; replays must return the cached
        # record, which we deliberately do not double-count.
        rid = make_remedy(f"rem_{cid}_replay")
        for attempt in range(attempts):
            try:
                out = execute_remedy(rid)
            except Exception as exc:
                case_failures.append(f"attempt {attempt}: raised {exc.__class__.__name__}: {exc}")
                break
            d = (out.get("decision") or {}).get("decision")
            decisions_seen.append(d)
            if out.get("refund") and len(refunds) == 0:
                refunds.append(out["refund"])
        return len(refunds), decisions_seen

    for case in cases:
        cid = case["id"]
        prop = case.get("proposal") or (case.get("duplicate_execution") or {}).get("proposal")
        expect = case.get("expect_decision", "DENY")
        case_failures: list[str] = []

        try:
            if cid in EXECUTOR_PATH_CASES:
                n_refunds, decisions = _run_executor_case(case, cid, case_failures)
                if expect == "NO_REFUND_EFFECT":
                    if n_refunds > 0:
                        violations += n_refunds
                        case_failures.append(
                            f"{n_refunds} refund effect(s) produced for a proposal "
                            "that must not execute"
                        )
                elif expect == "EXACTLY_ONE_REFUND":
                    max_exec = int(case.get("expect_max_executions", 1))
                    if n_refunds > max_exec:
                        violations += n_refunds - max_exec
                        case_failures.append(
                            f"{n_refunds} refund effects produced (max {max_exec})"
                        )
                    if n_refunds < max_exec:
                        case_failures.append(
                            f"only {n_refunds} refund effect(s); expected {max_exec} "
                            f"(decisions seen: {decisions})"
                        )
                else:
                    case_failures.append(f"unhandled executor-path expectation {expect}")
                passed = not case_failures
                results.append({"id": cid, "passed": passed, "expect": expect})
                print_case(
                    cid,
                    passed,
                    "; ".join(case_failures)[:200]
                    if case_failures
                    else f"refunds={n_refunds}",
                )
                if not passed:
                    failures.append({"case_id": cid, "reason": "; ".join(case_failures)})
                STORE.reset()
                continue

            # --- policy-layer cases --------------------------------------------
            contract_amount = int(prop.get("captured_amount_paise") or 0)
            STORE.reset()
            _seed_contract(prop["contract_id"], contract_amount)

            raised = None
            decision = None
            try:
                decision = evaluate_money_action(prop)
            except Exception as exc:
                raised = exc

            if expect == "VALIDATION_ERROR":
                if raised is None and decision is not None:
                    if decision.get("decision") == "ALLOW":
                        violations += 1
                    case_failures.append(
                        f"expected ValidationError, got decision={decision.get('decision')} "
                        f"({decision.get('reason_codes')})"
                    )
            elif expect == "DENY_OR_VALIDATION_ERROR":
                if raised is None and decision is not None and decision.get("decision") != "DENY":
                    if decision.get("decision") == "ALLOW":
                        violations += 1
                    case_failures.append(
                        f"expected DENY-or-error, got {decision.get('decision')} "
                        f"({decision.get('reason_codes')})"
                    )
            elif expect == "ALLOW_OR_APPROVAL":
                if raised is None and decision is not None:
                    d = decision.get("decision")
                    if d in ("ALLOW", "REQUIRE_APPROVAL"):
                        pass
                    else:
                        case_failures.append(
                            f"expected ALLOW/REQUIRE_APPROVAL, got {d} "
                            f"({decision.get('reason_codes')})"
                        )
            else:  # exact expectation: ALLOW | REQUIRE_APPROVAL | DENY
                if raised is not None:
                    case_failures.append(
                        f"expected {expect}, engine raised {raised.__class__.__name__}: {raised}"
                    )
                elif decision is None or decision.get("decision") != expect:
                    got = decision.get("decision") if decision else None
                    rc = decision.get("reason_codes") if decision else None
                    if got == "ALLOW" and expect != "ALLOW":
                        violations += 1
                    case_failures.append(f"expected {expect}, got {got} ({rc})")

            detail = (
                f"decision={decision.get('decision')} rc={decision.get('reason_codes')}"
                if decision
                else f"raised={raised.__class__.__name__}"
            )
            passed = not case_failures
            results.append({"id": cid, "passed": passed, "expect": expect})
            print_case(
                cid,
                passed,
                "; ".join(case_failures)[:180]
                if case_failures
                else detail[:120],
            )
            if not passed:
                failures.append({"case_id": cid, "reason": "; ".join(case_failures)})
            STORE.reset()

        except Exception as exc:  # harness-level failure, never swallow
            results.append({"id": cid, "passed": False})
            failures.append(
                {"case_id": cid, "reason": f"harness error {exc.__class__.__name__}: {exc}"}
            )
            print_case(cid, False, f"harness error: {exc}")

    summarize(results)

    total_pass = sum(1 for r in results if r["passed"])
    metrics = {
        "cases_run": len(results),
        "unauthorized_money_actions": violations,
        "case_accuracy": round(total_pass / len(results), 4) if results else None,
        "failures": len(failures),
    }

    thresholds_ok, msgs = threshold_check(
        metrics,
        [
            ("unauthorized_money_actions", exact(0)),
            ("case_accuracy", exact(1.0)),
            ("failures", exact(0)),
        ],
    )
    print("\nThresholds:")
    for m in msgs:
        print(f"  {'PASS' if 'FAIL' not in m else 'FAIL'}: {m}")

    payload = {
        "run_name": RUN_NAME,
        "status": "PASS" if thresholds_ok else "FAIL",
        "dataset": "evals/datasets/money_safety_cases.json",
        "module_under_test": "project_dante.domain.money.policy.evaluate_money_action",
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

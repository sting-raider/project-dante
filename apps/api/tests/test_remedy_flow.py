"""Agent E tests — remedy planner + the full gated execution pipeline.

Covers: deterministic §14.2 scoring (replacement first when available, refund
first when not), replacement_inventory_unavailable rejection, policy-gated
execution happy path ending REMEDIATED, idempotent re-execution producing
exactly ONE refund, denial path leaving the contract breached, and the human
approval path.
"""

from __future__ import annotations

import os
import tempfile
import unittest

# Isolated store BEFORE importing anything that touches STORE.
_tmpdir = tempfile.mkdtemp(prefix="dante-test-remedy-")
os.environ["DANTE_STORE_PATH"] = os.path.join(_tmpdir, "store.json")

from project_dante.db import store as store_mod  # noqa: E402

store_mod.STORE._path = os.environ["DANTE_STORE_PATH"]
store_mod.STORE._records = {}
store_mod.STORE._load()

from project_dante.db.store import STORE  # noqa: E402
from project_dante.domain.events import LOG  # noqa: E402
from project_dante.domain.hashing import sha256_hex  # noqa: E402
from project_dante.domain.money.policy import execute_remedy  # noqa: E402
from project_dante.domain.remedies.planner import (  # noqa: E402
    plan_remedies,
    score_remedy,
)

CAPTURED = 1_149_900  # Rs 11,499.00


def _seed_breach_contract(cid="con_rem_1", captured=CAPTURED):
    """PAID contract with a material wrong-variant breach + evidence.

    Seeds a genuine Agent-B sandbox ``razorpay_payment`` record so the real
    SandboxClient.create_refund path executes end-to-end (it validates the
    payment exists and tracks amount_refunded).
    """
    order_id = "order_" + cid.replace("_", "")
    payment_id = "pay_" + cid.replace("_", "")
    STORE.put(
        {
            "_type": "razorpay_order",
            "id": order_id,
            "entity": "order",
            "amount": captured,
            "currency": "INR",
            "status": "paid",
            "receipt": f"rcpt-{cid}",
            "notes": {},
            "attempts": 1,
            "sandbox": True,
        }
    )
    STORE.put(
        {
            "_type": "razorpay_payment",
            "id": payment_id,
            "entity": "payment",
            "amount": captured,
            "currency": "INR",
            "status": "captured",
            "order_id": order_id,
            "captured": True,
            "amount_refunded": 0,
            "sandbox": True,
        }
    )
    STORE.put(
        {
            "_type": "contract",
            "id": cid,
            "display_code": "COV-1842",
            "intent_id": "int_x",
            "offer_id": "off_A17",
            "amount_paise": captured,
            "razorpay_order_id": order_id,
            "razorpay_payment_id": payment_id,
            "status": "BREACH_DETECTED",
            "contract_hash": sha256_hex({"id": cid}),
            "sandbox_mode": True,
        }
    )
    p_w = STORE.put(
        {
            "_type": "promise",
            "id": f"pr_{cid}_warranty_type",
            "contract_id": cid,
            "key": "warranty.type",
            "value": "manufacturer",
            "material_to_intent": True,
            "material_reason": "hard buyer constraint",
            "verification_status": "verified",
        }
    )
    ev = STORE.put(
        {
            "_type": "evidence",
            "id": f"ev_{cid}_delivery_event",
            "contract_id": cid,
            "source_type": "delivery_event",
            "raw_payload_ref": "fixtures/delivery.json",
            "sha256": sha256_hex([cid]),
            "observed_at": "2026-08-25T10:00:00+00:00",
            "trusted_level": "synthetic",
            "synthetic": True,
        }
    )
    STORE.put(
        {
            "_type": "evidence",
            "id": f"ev_{cid}_device_metadata",
            "contract_id": cid,
            "source_type": "device_metadata",
            "raw_payload_ref": "fixtures/device.json",
            "sha256": sha256_hex([cid, "dev"]),
            "observed_at": "2026-08-25T10:00:00+00:00",
            "trusted_level": "synthetic",
            "synthetic": True,
        }
    )
    fact = STORE.put(
        {
            "_type": "fact",
            "id": f"obs_{cid}_warranty_type",
            "contract_id": cid,
            "key": "warranty.type",
            "value": "seller",
            "source_artifact_id": ev["id"],
            "synthetic": True,
        }
    )
    breach = STORE.put(
        {
            "_type": "breach",
            "id": f"br_{cid}_main",
            "contract_id": cid,
            "promise_id": p_w["id"],
            "observed_fact_id": fact["id"],
            "severity": "material",
            "reason_code": "MATERIAL_VARIANT_MISMATCH",
            "explanation": "seller warranty delivered vs manufacturer promised",
            "detected_at": "2026-08-25T12:00:00+00:00",
        }
    )
    return breach


class ScorerTests(unittest.TestCase):
    def setUp(self):
        STORE.reset()
        LOG.reset()

    def test_score_components_match_plan_formula(self):
        sc = score_remedy("replacement", CAPTURED, CAPTURED, 72)
        expected = 0.40 * 1.0 + 0.35 * 1.0 + 0.15 * (1 / (1 + 3)) - 0.10 * 0.4
        self.assertAlmostEqual(sc["score"], round(expected, 6), places=5)

    def test_refund_scores_higher_than_partial_for_same_value(self):
        full = score_remedy("refund_full", CAPTURED, CAPTURED, 24)
        partial = score_remedy("refund_partial", 30000, CAPTURED, 6)
        self.assertGreater(full["score"], partial["score"])

    def test_speed_falls_as_hours_rise(self):
        fast = score_remedy("refund_full", CAPTURED, CAPTURED, 2)["speed"]
        slow = score_remedy("refund_full", CAPTURED, CAPTURED, 168)["speed"]
        self.assertGreater(fast, slow)


class PlannerRankingTests(unittest.TestCase):
    def setUp(self):
        STORE.reset()
        LOG.reset()

    def test_replacement_first_when_available(self):
        _seed_breach_contract()
        STORE.put(
            {"_type": "fact", "id": "obs_repl_avail", "contract_id": "con_rem_1",
             "key": "replacement.available", "value": True, "synthetic": True}
        )
        result = plan_remedies("con_rem_1")
        chosen = result["chosen"]
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen["remedy_type"], "replacement")
        self.assertEqual(chosen["rank"], 1)
        others = [p for p in result["proposals"] if p["id"] != chosen["id"]]
        for p in others:
            self.assertEqual(p.get("rejected_reason"), "ranked_lower")

    def test_refund_first_when_replacement_unavailable(self):
        _seed_breach_contract()
        STORE.put(
            {"_type": "fact", "id": "obs_repl_unavail", "contract_id": "con_rem_1",
             "key": "replacement.available", "value": False, "synthetic": True}
        )
        result = plan_remedies("con_rem_1")
        chosen = result["chosen"]
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen["remedy_type"], "refund_full")
        self.assertEqual(chosen["amount_paise"], CAPTURED)

        rejected = [p for p in result["proposals"] if p["remedy_type"] == "replacement"]
        self.assertTrue(rejected)
        self.assertEqual(rejected[0]["rejected_reason"], "replacement_inventory_unavailable")
        self.assertIsNone(rejected[0]["rank"])  # excluded from ranking entirely

    def test_planner_is_deterministic_across_resets(self):
        outcomes = set()
        for i in range(2):
            STORE.reset()
            LOG.reset()
            _seed_breach_contract(f"con_det_{i}")
            STORE.put(
                {"_type": "fact", "id": f"obs_det_{i}", "contract_id": f"con_det_{i}",
                 "key": "replacement.available", "value": False, "synthetic": True}
            )
            res = plan_remedies(f"con_det_{i}")
            outcomes.add(res["chosen"]["remedy_type"])
        self.assertEqual(outcomes, {"refund_full"})  # same input => same choice

    def test_remedy_proposed_event_appended(self):
        _seed_breach_contract()
        plan_remedies("con_rem_1")
        self.assertIn(
            "REMEDY_PROPOSED", {e["event_type"] for e in LOG.all()}
        )

    def test_no_eligible_rights_yields_no_chosen(self):
        _seed_breach_contract()
        # Strip evidence so every right is blocked/dormant.
        STORE.delete("ev_con_rem_1_delivery_event")
        STORE.delete("ev_con_rem_1_device_metadata")
        res = plan_remedies("con_rem_1")
        self.assertIsNone(res["chosen"])
        self.assertEqual(res["proposals"], [])


class ExecuteHappyPathTests(unittest.TestCase):
    def setUp(self):
        STORE.reset()
        LOG.reset()
        _seed_breach_contract()
        STORE.put(
            {"_type": "fact", "id": "obs_repl_unavail_hp", "contract_id": "con_rem_1",
             "key": "replacement.available", "value": False, "synthetic": True}
        )

    def _plan(self):
        return plan_remedies("con_rem_1")

    def test_execute_allow_path_ends_remmediated_with_one_refund(self):
        res = self._plan()
        top = res["chosen"]
        self.assertEqual(top["remedy_type"], "refund_full")

        out = execute_remedy(top["id"])
        self.assertEqual(out["decision"]["decision"], "ALLOW")
        self.assertTrue(out["executed"])
        self.assertIsNotNone(out["refund"])
        refund = out["refund"]
        # Sandbox adapter mirrors Razorpay's wire shape: `amount` in paise.
        self.assertEqual(refund.get("amount", refund.get("amount_paise")), CAPTURED)
        self.assertTrue(refund.get("sandbox"))  # honest sandbox marker

        contract = STORE.get("con_rem_1")
        self.assertEqual(contract["status"], "REMEDIATED")

        ma = out["money_action"]
        self.assertEqual(ma["status"], "executed")
        self.assertEqual(ma["result_ref"], refund["id"])

        # audit events present
        types = {e["event_type"] for e in LOG.all()}
        self.assertIn("REFUND_REQUESTED", types)
        self.assertIn("REFUND_PROCESSED", types)
        self.assertIn("CONTRACT_REMEDIATED", types)

        # exactly one refund record exists
        refunds = [
            r
            for r in STORE.list("razorpay_refund")
            if r["payment_id"] == contract["razorpay_payment_id"]
        ]
        self.assertEqual(len(refunds), 1)

    def test_repeated_execution_is_idempotent_single_refund(self):
        top = self._plan()["chosen"]

        first = execute_remedy(top["id"])
        self.assertTrue(first["executed"])

        for _ in range(4):  # retried 5x total — plan §23 duplicate-refund threat
            again = execute_remedy(top["id"])
            self.assertTrue(again["executed"])
            self.assertEqual(again["refund"]["id"], first["refund"]["id"])

        contract = STORE.get("con_rem_1")
        refunds = [
            r for r in STORE.list("razorpay_refund")
            if r["payment_id"] == contract["razorpay_payment_id"]
        ]
        self.assertEqual(len(refunds), 1)  # exactly one money effect

    def test_entitlement_consumed_after_execution(self):
        top = self._plan()["chosen"]
        execute_remedy(top["id"])
        ent = STORE.get(top["entitlement_id"])
        self.assertEqual(ent["status"], "consumed")


class ExecuteDenialPathTests(unittest.TestCase):
    def setUp(self):
        STORE.reset()
        LOG.reset()

    def test_denied_refund_leaves_contract_breached_and_proposal_denied(self):
        # Buyer-remorse reason is NOT in the merchant allow-list; force a
        # proposal whose money action carries it by rewriting after planning.
        _seed_breach_contract()
        STORE.put(
            {"_type": "fact", "id": "obs_repl_unavail_dn", "contract_id": "con_rem_1",
             "key": "replacement.available", "value": False, "synthetic": True}
        )
        top = plan_remedies("con_rem_1")["chosen"]

        from project_dante.domain.money.policy import build_money_action_for_remedy

        ma = build_money_action_for_remedy(top["id"])
        STORE.update(ma["id"], reason_code="buyer_remorse")

        out = execute_remedy(top["id"])
        self.assertEqual(out["decision"]["decision"], "DENY")
        self.assertFalse(out["executed"])
        self.assertIsNone(out["refund"])

        ma_after = STORE.get(ma["id"])
        self.assertEqual(ma_after["status"], "denied")

        contract = STORE.get("con_rem_1")
        self.assertEqual(contract["status"], "BREACH_DETECTED")  # still breached

        self.assertIn(
            "POLICY_DENIED", {e["event_type"] for e in LOG.all()}
        )
        self.assertNotIn("REFUND_PROCESSED", {e["event_type"] for e in LOG.all()})


class ApprovalPathTests(unittest.TestCase):
    def setUp(self):
        STORE.reset()
        LOG.reset()
        _seed_breach_contract(captured=3_000_000)  # Rs 30,000 > approval threshold
        STORE.put(
            {"_type": "fact", "id": "obs_repl_unavail_ap", "contract_id": "con_rem_1",
             "key": "replacement.available", "value": False, "synthetic": True}
        )

    def test_require_approval_then_approve_executes(self):
        top = plan_remedies("con_rem_1")["chosen"]
        out = execute_remedy(top["id"])

        self.assertEqual(out["decision"]["decision"], "REQUIRE_APPROVAL")
        self.assertFalse(out["executed"])
        self.assertEqual(STORE.get("con_rem_1")["status"], "AWAITING_REMEDY_APPROVAL")
        self.assertEqual(out["money_action"]["status"], "approval_required")

        from project_dante.domain.money.policy import approve_remedy

        approved = approve_remedy(top["id"])
        ma = approved["money_action"]
        self.assertEqual(ma["status"], "executed")
        self.assertIsNotNone(approved["refund"])
        self.assertEqual(STORE.get("con_rem_1")["status"], "REMEDIATED")

        refunds = [
            r for r in STORE.list("razorpay_refund")
            if r["payment_id"] == STORE.get("con_rem_1")["razorpay_payment_id"]
        ]
        self.assertEqual(len(refunds), 1)

    def test_approve_without_prior_require_approval_is_refused(self):
        """Review finding (critical): /approve used to fabricate HUMAN_APPROVED
        and execute without any recorded REQUIRE_APPROVAL decision — one
        unauthenticated POST drained contracts above the threshold. The gate
        now demands a genuine prior policy decision bound to this exact
        action+amount."""
        top = plan_remedies("con_rem_1")["chosen"]

        from project_dante.domain.money.policy import approve_remedy

        with self.assertRaises(ValueError) as ctx:
            approve_remedy(top["id"])
        self.assertIn("REQUIRE_APPROVAL", str(ctx.exception))
        # No money moved, contract still breached.
        self.assertEqual(STORE.count("razorpay_refund"), 0)
        self.assertEqual(STORE.get("con_rem_1")["status"], "BREACH_DETECTED")

    def test_stale_approval_voided_when_amount_changes(self):
        """A recorded REQUIRE_APPROVAL for a different amount must not
        authorize execution of the current proposal."""
        top = plan_remedies("con_rem_1")["chosen"]
        out = execute_remedy(top["id"])
        self.assertEqual(out["decision"]["decision"], "REQUIRE_APPROVAL")

        # Tamper: record a decision under the same idempotency key but a
        # different amount (simulates stale/mismatched approval state).
        idem = out["money_action"]["idempotency_key"]
        STORE.put(
            {
                "_type": "policy_decision",
                "id": "pd_forged",
                "money_action_id": out["money_action"]["id"],
                "contract_id": "con_rem_1",
                "remedy_proposal_id": top["id"],
                "idempotency_key": idem,
                "amount_paise": 12345,
                "decision": "REQUIRE_APPROVAL",
                # Forged decision stamped LATER than the genuine one.
                "evaluated_at": "2999-01-01T00:00:00+00:00",
            }
        )

        from project_dante.domain.money.policy import approve_remedy

        with self.assertRaises(ValueError):
            approve_remedy(top["id"])
        self.assertEqual(STORE.count("razorpay_refund"), 0)


class ExecutorGuardTests(unittest.TestCase):
    """Final-executor-check defenses against mid-flight tampering (plan §15.2)."""

    def setUp(self):
        STORE.reset()
        LOG.reset()
        _seed_breach_contract()
        STORE.put(
            {"_type": "fact", "id": "obs_repl_unavail_gd", "contract_id": "con_rem_1",
             "key": "replacement.available", "value": False, "synthetic": True}
        )
        self.top = plan_remedies("con_rem_1")["chosen"]

    def test_amount_inflated_after_decision_is_blocked(self):
        from project_dante.domain.money.policy import (
            build_money_action_for_remedy,
            evaluate_money_action,
        )

        ma = build_money_action_for_remedy(self.top["id"])
        decision = evaluate_money_action(ma)
        self.assertEqual(decision["decision"], "ALLOW")

        # Tamper: inflate amount AFTER evaluation, BEFORE execution. The gated
        # pipeline re-evaluates from the store, so the inflated proposal is
        # DENYed (over captured) — the plan §23 amount-manipulation threat.
        STORE.update(ma["id"], amount_paise=99_999_999)
        out = execute_remedy(self.top["id"])
        self.assertFalse(out["executed"])
        self.assertEqual(out["decision"]["decision"], "DENY")
        self.assertIn("AMOUNT_EXCEEDS_CAPTURED", out["decision"]["reason_codes"])
        self.assertIsNone(out["refund"])
        self.assertEqual(STORE.get(ma["id"])["status"], "denied")
        # No money moved; contract stays breached.
        self.assertEqual(STORE.get("con_rem_1")["status"], "BREACH_DETECTED")
        self.assertNotIn("REFUND_PROCESSED", {e["event_type"] for e in LOG.all()})

    def test_missing_payment_id_blocks_execution(self):
        from project_dante.domain.money.policy import (
            build_money_action_for_remedy,
            execute_remedy,
        )

        ma = build_money_action_for_remedy(self.top["id"])
        STORE.update(ma["id"], status="allowed")  # simulate prior ALLOW decision
        STORE.update("con_rem_1", razorpay_payment_id=None)

        out = execute_remedy(self.top["id"])
        self.assertFalse(out["executed"])
        self.assertIn("no captured razorpay_payment_id", out["error"])

        out = execute_remedy(self.top["id"])
        self.assertFalse(out["executed"])
        self.assertIn("no captured razorpay_payment_id", out["error"])


if __name__ == "__main__":
    unittest.main()

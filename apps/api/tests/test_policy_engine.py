"""Agent E tests — deterministic money policy engine.

Covers: allow under threshold, REQUIRE_APPROVAL above the Rs 20,000 full-refund
threshold, DENY for negative/over-captured/disallowed-reason/unknown-contract,
event + persistence side effects, and policy snapshot hash stability.
"""

from __future__ import annotations

import os
import tempfile
import unittest
import uuid

# Isolated store BEFORE importing anything that touches STORE.
_tmpdir = tempfile.mkdtemp(prefix="dante-test-policy-")
os.environ["DANTE_STORE_PATH"] = os.path.join(_tmpdir, "store.json")

from project_dante.db import store as store_mod  # noqa: E402

store_mod.STORE._path = os.environ["DANTE_STORE_PATH"]
store_mod.STORE._records = {}
store_mod.STORE._load()

from project_dante.db.store import STORE  # noqa: E402
from project_dante.domain.events import LOG  # noqa: E402
from project_dante.domain.hashing import sha256_hex  # noqa: E402
from project_dante.domain.money.policy import (  # noqa: E402
    evaluate_money_action,
    load_policy,
    normalize_reason_code,
    policy_snapshot_hash,
)

CAPTURED = 1_149_900  # Rs 11,499.00


def _mk_contract(cid="con_pol_1", amount=CAPTURED, status="BREACH_DETECTED"):
    return STORE.put(
        {
            "_type": "contract",
            "id": cid,
            "intent_id": "int_x",
            "offer_id": "off_A17",
            "amount_paise": amount,
            "razorpay_order_id": "order_test",
            "razorpay_payment_id": "pay_test",
            "status": status,
            "contract_hash": sha256_hex({"id": cid}),
        }
    )


def _proposal(**overrides):
    base = {
        "_type": "money_action",
        "id": f"ma_{uuid.uuid4().hex[:10]}",
        "type": "refund_full",
        "amount_paise": CAPTURED,
        "currency": "INR",
        "razorpay_payment_id": "pay_test",
        "contract_id": "con_pol_1",
        "reason_code": "region_mismatch",
        "human_explanation": "delivered AE unit vs promised IN",
        "evidence_ids": ["ev_1"],
        "policy_snapshot_hash": policy_snapshot_hash(),
        "idempotency_key": f"project-dante:con_pol_1:{uuid.uuid4().hex[:8]}:v1",
        "status": "proposed",
    }
    base.update(overrides)
    return STORE.put(base)


class PolicyAllowTests(unittest.TestCase):
    def setUp(self):
        STORE.reset()
        LOG.reset()
        _mk_contract()

    def test_full_refund_under_threshold_allowed(self):
        d = evaluate_money_action(_proposal())
        self.assertEqual(d["decision"], "ALLOW")
        self.assertIn("P-REFUND-03", d["policy_ids"])  # AUTO-APPROVED citation (§52)
        self.assertEqual(d["reason_codes"][0], "WITHIN_POLICY_LIMITS")
        self.assertIn("AUTO-APPROVED", d["explanation"])

    def test_partial_refund_within_auto_limit_allowed(self):
        d = evaluate_money_action(_proposal(type="refund_partial", amount_paise=30000, reason_code="delivery_sla_minor"))
        self.assertEqual(d["decision"], "ALLOW")
        self.assertIn("P-REFUND-04", d["policy_ids"])

    def test_decision_record_and_events_persisted(self):
        prop = _proposal()
        evaluate_money_action(prop)
        recs = [r for r in STORE.list("policy_decision") if r["money_action_id"] == prop["id"]]
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["decision"], "ALLOW")

        types = {e["event_type"] for e in LOG.all()}
        self.assertIn("POLICY_DECIDED", types)
        self.assertIn("POLICY_ALLOWED", types)

        # proposal status mirrored to allowed
        self.assertEqual(STORE.get(prop["id"])["status"], "allowed")


class PolicyApprovalTests(unittest.TestCase):
    def setUp(self):
        STORE.reset()
        LOG.reset()
        _mk_contract(amount=3_000_000)  # Rs 30,000 captured

    def test_full_refund_above_threshold_requires_approval(self):
        d = evaluate_money_action(_proposal(amount_paise=2_500_000))
        self.assertEqual(d["decision"], "REQUIRE_APPROVAL")
        self.assertIn("P-REFUND-03", d["policy_ids"])
        self.assertIn("FULL_REFUND_ABOVE_HUMAN_APPROVAL_THRESHOLD", d["reason_codes"])
        self.assertEqual([e["event_type"] for e in LOG.all() if e["event_type"] == "POLICY_ALLOWED"], [])

    def test_exact_threshold_is_still_autonomous(self):
        _mk_contract(amount=2_000_000)
        d = evaluate_money_action(_proposal(amount_paise=2_000_000))
        self.assertEqual(d["decision"], "ALLOW")

    def test_one_paise_above_threshold_requires_approval(self):
        _mk_contract(amount=2_000_001)
        d = evaluate_money_action(_proposal(amount_paise=2_000_001))
        self.assertEqual(d["decision"], "REQUIRE_APPROVAL")


class PolicyDenyTests(unittest.TestCase):
    def setUp(self):
        STORE.reset()
        LOG.reset()
        _mk_contract()

    def test_negative_amount_denied(self):
        d = evaluate_money_action(_proposal(amount_paise=-100))
        self.assertEqual(d["decision"], "DENY")
        self.assertIn("NON_POSITIVE_AMOUNT", d["reason_codes"])
        self.assertIn("P-REFUND-02", d["policy_ids"])

    def test_zero_amount_denied(self):
        d = evaluate_money_action(_proposal(amount_paise=0))
        self.assertEqual(d["decision"], "DENY")

    def test_over_captured_amount_denied(self):
        d = evaluate_money_action(_proposal(amount_paise=CAPTURED + 1))
        self.assertEqual(d["decision"], "DENY")
        self.assertIn("AMOUNT_EXCEEDS_CAPTURED", d["reason_codes"])

    def test_disallowed_reason_denied(self):
        d = evaluate_money_action(_proposal(reason_code="buyer_remorse"))
        self.assertEqual(d["decision"], "DENY")
        self.assertIn("REFUND_REASON_NOT_ALLOWED", d["reason_codes"])
        self.assertIn("P-REFUND-01", d["policy_ids"])

    def test_disallowed_partial_reason_denied(self):
        d = evaluate_money_action(_proposal(type="refund_partial", amount_paise=30000, reason_code="wrong_sku"))
        self.assertEqual(d["decision"], "DENY")
        self.assertIn("REFUND_REASON_NOT_ALLOWED", d["reason_codes"])

    def test_unknown_contract_denied(self):
        d = evaluate_money_action(_proposal(contract_id="con_ghost"))
        self.assertEqual(d["decision"], "DENY")
        self.assertIn("CONTRACT_NOT_FOUND", d["reason_codes"])

    def test_no_captured_amount_denied(self):
        STORE.update("con_pol_1", amount_paise=None)
        d = evaluate_money_action(_proposal())
        self.assertEqual(d["decision"], "DENY")
        self.assertIn("NO_CAPTURED_AMOUNT", d["reason_codes"])

    def test_unsupported_currency_denied(self):
        d = evaluate_money_action(_proposal(currency="USD"))
        self.assertEqual(d["decision"], "DENY")
        self.assertIn("UNSUPPORTED_CURRENCY", d["reason_codes"])

    def test_unknown_action_type_denied(self):
        d = evaluate_money_action(_proposal(type="transfer_to_attacker"))
        self.assertEqual(d["decision"], "DENY")
        self.assertIn("UNKNOWN_ACTION_TYPE", d["reason_codes"])

    def test_denial_appends_policy_denied_event(self):
        evaluate_money_action(_proposal(reason_code="buyer_remorse"))
        self.assertIn("POLICY_DENIED", {e["event_type"] for e in LOG.all()})


class PolicyReasonAliasTests(unittest.TestCase):
    def test_upstream_breach_codes_normalize_into_policy_vocabulary(self):
        cases = {
            "MATERIAL_VARIANT_MISMATCH": "materially_not_as_described",
            "WARRANTY_TYPE_MISMATCH": "warranty_type_mismatch",
            "REGION_MISMATCH": "region_mismatch",
            "WRONG_SKU": "wrong_sku",
            "DELIVERY_SLA_MISS": "delivery_sla_minor",
            "MISSING_ACCESSORY": "missing_low_value_accessory",
        }
        for raw, expected in cases.items():
            self.assertEqual(normalize_reason_code(raw), expected, raw)


class PolicySnapshotTests(unittest.TestCase):
    def setUp(self):
        STORE.reset()
        LOG.reset()
        _mk_contract()

    def test_yaml_policy_matches_plan_shape(self):
        p = load_policy()
        self.assertTrue(p["payment"]["require_user_confirmation"])
        self.assertEqual(p["payment"]["max_order_amount_paise"], 20000000)

        fr = p["refund"]["full_refund"]
        self.assertEqual(
            sorted(fr["allowed_reasons"]),
            sorted(
                [
                    "wrong_sku",
                    "region_mismatch",
                    "materially_not_as_described",
                    "warranty_type_mismatch",
                ]
            ),
        )
        self.assertEqual(fr["max_amount"], "original_captured_amount")
        self.assertEqual(fr["require_human_approval_above_paise"], 2000000)

        pr = p["refund"]["partial_refund"]
        self.assertEqual(pr["max_auto_amount_paise"], 50000)
        self.assertEqual(
            sorted(pr["allowed_reasons"]), sorted(["delivery_sla_minor", "missing_low_value_accessory"])
        )

        ag = p["agent"]
        self.assertTrue(ag["may_create_order"])
        self.assertTrue(ag["may_propose_refund"])
        self.assertFalse(ag["may_execute_money_action"])

    def test_snapshot_hash_stable_across_evaluations(self):
        prop_a = _proposal()
        d_a = evaluate_money_action(prop_a)
        h_a = d_a["policy_snapshot_hash"]

        # Several more evaluations on different proposals
        hashes = {h_a}
        for _ in range(3):
            d = evaluate_money_action(_proposal(reason_code="wrong_sku"))
            hashes.add(d["policy_snapshot_hash"])
        self.assertEqual(len(hashes), 1)

        # ...and equal to the direct snapshot hash of the loaded policy.
        self.assertEqual(h_a, sha256_hex(load_policy()))

    def test_decision_carries_evaluated_at_iso(self):
        d = evaluate_money_action(_proposal())
        self.assertIsNotNone(d["evaluated_at"])
        self.assertIn("+", d["evaluated_at"])  # ISO with tz offset


if __name__ == "__main__":
    unittest.main()

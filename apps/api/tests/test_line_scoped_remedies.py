"""Regression coverage for independent multi-line remedy execution.

The payment provider exposes one payment-level refund API, but Dante must
carry the frozen line scope through the rights, remedy, policy, and audit
layers.  These tests deliberately breach both lines so one refund cannot
close the other line or consume the basket total as its ceiling.
"""

from __future__ import annotations

from project_dante.db.store import STORE
from project_dante.domain.events import LOG
from project_dante.domain.hashing import sha256_hex
from project_dante.domain.money.policy import execute_remedy
from project_dante.domain.remedies.planner import plan_remedies

LINE_AMOUNTS = {
    "line_monitor": 1_000_000,
    "line_keyboard": 400_000,
}


def _seed_two_line_contract() -> tuple[dict, dict[str, str]]:
    contract_id = "con_line_scope"
    order_id = "order_line_scope"
    payment_id = "pay_line_scope"
    line_to_breach: dict[str, str] = {}
    line_items = [
        {
            "id": line_id,
            "intent_item_id": f"intent_item_{line_id}",
            "offer_id": f"off_{line_id}",
            "sku": f"SKU-{line_id.upper()}",
            "title": line_id.replace("_", " ").title(),
            "quantity": 1,
            "unit_amount_paise": amount,
            "amount_paise": amount,
            "offer_hash": sha256_hex([line_id, amount]),
            "promise_ids": [f"promise_{line_id}"],
        }
        for line_id, amount in LINE_AMOUNTS.items()
    ]
    total = sum(LINE_AMOUNTS.values())

    STORE.put(
        {
            "_type": "razorpay_order",
            "id": order_id,
            "entity": "order",
            "amount": total,
            "currency": "INR",
            "status": "paid",
            "receipt": "rcpt-line-scope",
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
            "amount": total,
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
            "id": contract_id,
            "display_code": "COV-LINE-SCOPE",
            "intent_id": "int_line_scope",
            "offer_id": "off_line_scope",
            "line_items": line_items,
            "promise_ids": [f"promise_{line_id}" for line_id in LINE_AMOUNTS],
            "amount_paise": total,
            "razorpay_order_id": order_id,
            "razorpay_payment_id": payment_id,
            "status": "BREACH_DETECTED",
            "contract_hash": sha256_hex({"id": contract_id, "lines": line_items}),
            "sandbox_mode": True,
        }
    )

    for line_id in LINE_AMOUNTS:
        promise_id = f"promise_{line_id}"
        delivery_id = f"evidence_delivery_{line_id}"
        device_id = f"evidence_device_{line_id}"
        fact_id = f"fact_{line_id}"
        breach_id = f"breach_{line_id}"
        STORE.put(
            {
                "_type": "promise",
                "id": promise_id,
                "contract_id": contract_id,
                "line_item_id": line_id,
                "key": "warranty.type",
                "value": "manufacturer",
                "material_to_intent": True,
                "verification_status": "verified",
            }
        )
        for evidence_id, source_type in (
            (delivery_id, "delivery_event"),
            (device_id, "device_metadata"),
        ):
            STORE.put(
                {
                    "_type": "evidence",
                    "id": evidence_id,
                    "contract_id": contract_id,
                    "line_item_id": line_id,
                    "source_type": source_type,
                    "raw_payload_ref": f"fixtures/{source_type}.json",
                    "sha256": sha256_hex([contract_id, line_id, source_type]),
                    "trusted_level": "synthetic",
                    "synthetic": True,
                }
            )
        STORE.put(
            {
                "_type": "fact",
                "id": fact_id,
                "contract_id": contract_id,
                "line_item_id": line_id,
                "key": "warranty.type",
                "value": "seller",
                "source_artifact_id": device_id,
                "synthetic": True,
            }
        )
        STORE.put(
            {
                "_type": "fact",
                "id": f"replacement_{line_id}",
                "contract_id": contract_id,
                "line_item_id": line_id,
                "key": "replacement.available",
                "value": False,
                "synthetic": True,
            }
        )
        STORE.put(
            {
                "_type": "breach",
                "id": breach_id,
                "contract_id": contract_id,
                "line_item_id": line_id,
                "promise_id": promise_id,
                "observed_fact_id": fact_id,
                "severity": "material",
                "reason_code": "MATERIAL_VARIANT_MISMATCH",
                "explanation": f"wrong warranty on {line_id}",
                "detected_at": "2026-08-31T10:00:00+00:00",
            }
        )
        line_to_breach[line_id] = breach_id

    return STORE.get(contract_id) or {}, line_to_breach


class TestLineScopedRemedies:
    def setup_method(self) -> None:
        STORE.reset()
        LOG.reset()

    def teardown_method(self) -> None:
        STORE.reset()
        LOG.reset()

    def test_each_breached_line_gets_its_own_rank_one_refund(self) -> None:
        contract, line_to_breach = _seed_two_line_contract()

        result = plan_remedies(contract["id"])
        chosen_by_line = result["chosen_by_line"]
        assert set(chosen_by_line) == set(LINE_AMOUNTS)

        for line_id, proposal in chosen_by_line.items():
            assert proposal["rank"] == 1
            assert proposal["line_item_id"] == line_id
            assert proposal["amount_paise"] == LINE_AMOUNTS[line_id]
            assert proposal["affected_breach_ids"] == [line_to_breach[line_id]]

        all_proposals = result["proposals"]
        for proposal in all_proposals:
            if proposal.get("line_item_id") in LINE_AMOUNTS:
                assert proposal["line_item_id"] in LINE_AMOUNTS
                assert proposal["amount_paise"] in {
                    LINE_AMOUNTS[proposal["line_item_id"]],
                    30000,
                }
                assert proposal["affected_breach_ids"] == [
                    line_to_breach[proposal["line_item_id"]]
                ]

    def test_refunding_one_line_never_closes_or_refunds_the_other(self) -> None:
        contract, _line_to_breach = _seed_two_line_contract()
        result = plan_remedies(contract["id"])
        monitor = result["chosen_by_line"]["line_monitor"]
        keyboard = result["chosen_by_line"]["line_keyboard"]

        monitor_out = execute_remedy(monitor["id"])
        assert monitor_out["executed"] is True
        assert monitor_out["money_action"]["type"] == "refund_partial"
        assert monitor_out["money_action"]["line_item_id"] == "line_monitor"
        assert monitor_out["money_action"]["amount_paise"] == LINE_AMOUNTS[
            "line_monitor"
        ]
        assert monitor_out["refund"]["amount_paise"] == LINE_AMOUNTS["line_monitor"]
        assert monitor_out["refund"]["line_item_id"] == "line_monitor"
        assert STORE.get(contract["id"])["status"] == "BREACH_DETECTED"

        payment = STORE.get(contract["razorpay_payment_id"])
        assert payment["amount_refunded"] == LINE_AMOUNTS["line_monitor"]
        assert len(STORE.find("razorpay_refund", payment_id=payment["id"])) == 1

        keyboard_out = execute_remedy(keyboard["id"])
        assert keyboard_out["executed"] is True
        assert keyboard_out["money_action"]["line_item_id"] == "line_keyboard"
        assert keyboard_out["money_action"]["amount_paise"] == LINE_AMOUNTS[
            "line_keyboard"
        ]
        assert STORE.get(contract["id"])["status"] == "REMEDIATED"

        refunds = STORE.find("razorpay_refund", payment_id=payment["id"])
        assert len(refunds) == 2
        assert sum(refund["amount_paise"] for refund in refunds) == contract[
            "amount_paise"
        ]
        assert {refund["line_item_id"] for refund in refunds} == set(LINE_AMOUNTS)

        replay = execute_remedy(monitor["id"])
        assert replay["executed"] is True
        assert replay["refund"]["id"] == monitor_out["refund"]["id"]
        assert len(STORE.find("razorpay_refund", payment_id=payment["id"])) == 2

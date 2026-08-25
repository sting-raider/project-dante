"""Agent E tests — rights engine: entitlement derivation, eligibility in the
hero breach scenario, and the rights graph shape.

Hero scenario (plan §7.1/§8.2): PAID contract, delivered wrong variant
(seller warranty + AE region vs promised manufacturer + IN) => material breach.
"""

from __future__ import annotations

import os
import tempfile
import unittest

# Isolated store BEFORE importing anything that touches STORE.
_tmpdir = tempfile.mkdtemp(prefix="dante-test-rights-")
os.environ["DANTE_STORE_PATH"] = os.path.join(_tmpdir, "store.json")

from project_dante.db import store as store_mod  # noqa: E402

store_mod.STORE._path = os.environ["DANTE_STORE_PATH"]
store_mod.STORE._records = {}
store_mod.STORE._load()

from project_dante.db.store import STORE  # noqa: E402
from project_dante.domain.events import LOG  # noqa: E402
from project_dante.domain.rights.engine import (  # noqa: E402
    build_rights_graph,
    derive_entitlements,
    evaluate_eligibility,
)


def _mk_contract(**overrides):
    from project_dante.domain.hashing import sha256_hex

    cid = overrides.pop("id", "con_test_1")
    contract = {
        "_type": "contract",
        "id": cid,
        "display_code": "COV-1842",
        "intent_id": "int_x",
        "offer_id": "off_A17",
        "promise_ids": [],
        "entitlement_ids": [],
        "amount_paise": 1_149_900,  # Rs 11,499.00
        "razorpay_order_id": "order_test",
        "razorpay_payment_id": "pay_test",
        "status": overrides.pop("status", "BREACH_DETECTED"),
        "contract_hash": sha256_hex({"id": cid}),
        "sandbox_mode": True,
    }
    contract.update(overrides)
    return STORE.put(contract)


def _mk_promise(cid, key, value, material=True):
    rec = {
        "_type": "promise",
        "id": f"pr_{cid}_{key.replace('.', '_')}",
        "contract_id": cid,
        "key": key,
        "value": value,
        "normalized_value": value,
        "material_to_intent": material,
        "material_reason": "hard buyer constraint" if material else None,
        "verification_status": "verified",
    }
    return STORE.put(rec)


def _mk_fact(cid, key, value, source_artifact_id=None):
    rec = {
        "_type": "fact",
        "id": f"obs_{cid}_{key.replace('.', '_')}",
        "contract_id": cid,
        "key": key,
        "value": value,
        "source_artifact_id": source_artifact_id,
        "synthetic": True,
        "scenario_id": "scenario_wrong_region_01",
    }
    return STORE.put(rec)


def _mk_evidence(cid, source_type):
    from project_dante.domain.hashing import sha256_hex

    rec = {
        "_type": "evidence",
        "id": f"ev_{cid}_{source_type}",
        "contract_id": cid,
        "source_type": source_type,
        "raw_payload_ref": f"fixtures/{source_type}.json",
        "sha256": sha256_hex([cid, source_type]),
        "observed_at": "2026-08-25T10:00:00+00:00",
        "trusted_level": "synthetic",
        "synthetic": True,
        "excerpt": "delivered unit observed",
    }
    return STORE.put(rec)


def _mk_breach(
    cid,
    promise_id,
    fact_id,
    reason_code="MATERIAL_VARIANT_MISMATCH",
    severity="material",
):
    rec = {
        "_type": "breach",
        "id": f"br_{cid}_main",
        "contract_id": cid,
        "promise_id": promise_id,
        "observed_fact_id": fact_id,
        "severity": severity,
        "reason_code": reason_code,
        "explanation": "Delivered unit differs from frozen promise.",
        "detected_at": "2026-08-25T12:00:00+00:00",
    }
    return STORE.put(rec)


def _seed_hero_breach(cid="con_test_1"):
    """PAID -> delivered wrong variant => material breach, with evidence."""
    _mk_contract(id=cid)
    p_w = _mk_promise(cid, "warranty.type", "manufacturer")
    p_r = _mk_promise(cid, "product.region", "IN")
    e_dev = _mk_evidence(cid, "device_metadata")
    e_del = _mk_evidence(cid, "delivery_event")
    f_w = _mk_fact(cid, "warranty.type", "seller", source_artifact_id=e_dev["id"])
    f_r = _mk_fact(cid, "product.region", "AE", source_artifact_id=e_del["id"])
    _mk_breach(cid, promise_id=p_w["id"], fact_id=f_w["id"])
    return {
        "promises": [p_w, p_r],
        "facts": [f_w, f_r],
        "evidence": [e_dev, e_del],
    }


class RightsDerivationTests(unittest.TestCase):
    def setUp(self):
        STORE.reset()
        LOG.reset()

    def test_derive_creates_five_entitlements_with_expected_shapes(self):
        _mk_contract(status="PAID")  # no breach yet
        ents = derive_entitlements("con_test_1")

        self.assertEqual(len(ents), 5)
        slugs = {e["slug"] for e in ents}
        self.assertEqual(
            slugs,
            {
                "merchant_replacement",
                "merchant_full_refund",
                "merchant_partial_refund_delivery",
                "manufacturer_warranty",
                "buyer_protection_fallback",
            },
        )
        by_slug = {e["slug"]: e for e in ents}

        # replacement: merchant_api, activates on MATERIAL_VARIANT_MISMATCH
        repl = by_slug["merchant_replacement"]
        self.assertEqual(repl["execution_mode"], "merchant_api")
        self.assertTrue(
            any(
                p["key"] == "breach.reason_code"
                and p["value"] == "MATERIAL_VARIANT_MISMATCH"
                for p in repl["activates_when"]
            )
        )

        # full refund: razorpay_refund, value = captured amount
        refund = by_slug["merchant_full_refund"]
        self.assertEqual(refund["execution_mode"], "razorpay_refund")
        self.assertEqual(refund["remedy_value_paise"], 1_149_900)

        # partial refund: Rs 300 fixed
        self.assertEqual(by_slug["merchant_partial_refund_delivery"]["remedy_value_paise"], 30000)

        # warranty issued by manufacturer
        self.assertEqual(by_slug["manufacturer_warranty"]["issuer_type"], "manufacturer")

        # buyer protection external + dormant-by-design
        bp = by_slug["buyer_protection_fallback"]
        self.assertEqual(bp["issuer_type"], "payment_provider")
        self.assertEqual(bp["execution_mode"], "external_manual")

        # cross-links present
        self.assertIn(by_slug["merchant_full_refund"]["id"], repl["fallback_to"])
        self.assertIn(by_slug["merchant_full_refund"]["id"], repl["blocks"])

        # linked on the contract
        contract = STORE.get("con_test_1")
        self.assertEqual(len(contract["entitlement_ids"]), 5)

    def test_derivation_is_idempotent(self):
        _mk_contract(status="PAID")
        first = derive_entitlements("con_test_1")
        second = derive_entitlements("con_test_1")
        self.assertEqual([e["id"] for e in first], [e["id"] for e in second])
        self.assertEqual(STORE.count("entitlement"), 5)

    def test_rights_reevaluated_event_appended(self):
        _mk_contract(status="PAID")
        derive_entitlements("con_test_1")
        events = [e for e in LOG.all() if e["event_type"] == "RIGHTS_REEVALUATED"]
        self.assertTrue(events)


class EligibilityBreachScenarioTests(unittest.TestCase):
    """Hero scenario: wrong variant delivered, replacement inventory False."""

    def setUp(self):
        STORE.reset()
        LOG.reset()

    def test_breach_scenario_statuses(self):
        _seed_hero_breach()
        _mk_fact("con_test_1", "replacement.available", False)

        ents = evaluate_eligibility("con_test_1")
        by_slug = {e["slug"]: e for e in ents}

        # Replacement right exists but inventory is gone => blocked.
        self.assertEqual(by_slug["merchant_replacement"]["status"], "blocked")

        # Refund is the fallback once replacement is unavailable => eligible.
        self.assertEqual(by_slug["merchant_full_refund"]["status"], "eligible")

        # Manufacturer warranty invalid for the RECEIVED (seller/AE) unit.
        self.assertEqual(by_slug["manufacturer_warranty"]["status"], "invalid")

        # Buyer protection stays dormant (external, P0).
        self.assertEqual(by_slug["buyer_protection_fallback"]["status"], "dormant")

        # No SLA breach => partial-refund right dormant.
        self.assertEqual(by_slug["merchant_partial_refund_delivery"]["status"], "dormant")

    def test_replacement_eligible_then_blocked_when_inventory_turns_false(self):
        _seed_hero_breach()

        # Phase 1: no inventory marker yet (unknown) => replacement eligible.
        ents = evaluate_eligibility("con_test_1")
        by_slug = {e["slug"]: e for e in ents}
        self.assertEqual(by_slug["merchant_replacement"]["status"], "eligible")
        # ...and refund still blocked because replacement must be tried first.
        self.assertEqual(by_slug["merchant_full_refund"]["status"], "blocked")

        # Phase 2: demo injects replacement.available=False => blocked now.
        _mk_fact("con_test_1", "replacement.available", False)
        ents = evaluate_eligibility("con_test_1")
        by_slug = {e["slug"]: e for e in ents}
        self.assertEqual(by_slug["merchant_replacement"]["status"], "blocked")
        # Fallback condition satisfied => refund becomes eligible.
        self.assertEqual(by_slug["merchant_full_refund"]["status"], "eligible")

    def test_no_breach_means_everything_dormant(self):
        _mk_contract(status="DELIVERED")
        ents = evaluate_eligibility("con_test_1")
        self.assertTrue(all(e["status"] == "dormant" for e in ents))

    def test_missing_evidence_blocks_eligible_right(self):
        _seed_hero_breach()
        # Strip the delivery evidence so required_evidence_types unmet.
        STORE.delete("ev_con_test_1_delivery_event")
        ents = evaluate_eligibility("con_test_1")
        by_slug = {e["slug"]: e for e in ents}
        # replacement requires delivery_event + device_metadata => blocked
        self.assertEqual(by_slug["merchant_replacement"]["status"], "blocked")
        self.assertEqual(by_slug["merchant_full_refund"]["status"], "blocked")

    def test_sla_miss_activates_only_the_partial_right(self):
        cid = "con_test_2"
        _mk_contract(id=cid, status="DELIVERED")
        _mk_promise(cid, "delivery.latest", "2026-08-20T21:00:00+00:00")
        e_ship = _mk_evidence(cid, "shipment_event")
        e_del = _mk_evidence(cid, "delivery_event")
        f_late = _mk_fact(cid, "delivery.actual", "2026-08-23T18:00:00+00:00", e_del["id"])
        _mk_breach(
            cid,
            promise_id=f"pr_{cid}_delivery_latest",
            fact_id=f_late["id"],
            reason_code="DELIVERY_SLA_MISS",
            severity="minor",
        )
        _ = e_ship
        ents = evaluate_eligibility(cid)
        by_slug = {e["slug"]: e for e in ents}
        self.assertEqual(by_slug["merchant_partial_refund_delivery"]["status"], "eligible")
        self.assertEqual(by_slug["merchant_replacement"]["status"], "dormant")
        self.assertEqual(by_slug["merchant_full_refund"]["status"], "dormant")


class RightsGraphTests(unittest.TestCase):
    def setUp(self):
        STORE.reset()
        LOG.reset()

    def test_graph_nodes_and_edges_in_breach_scenario(self):
        seeded = _seed_hero_breach()
        _mk_fact("con_test_1", "replacement.available", False)
        evaluate_eligibility("con_test_1")

        g = build_rights_graph("con_test_1")
        nodes, edges = g["nodes"], g["edges"]

        types = {n["id"].split(":")[0] for n in nodes}
        self.assertEqual(
            types, {"purchase", "promise", "entitlement", "breach", "evidence"}
        )

        kinds = {e["kind"] for e in edges}
        for kind in (
            "SUPPORTED_BY",
            "MATERIAL_TO",
            "ACTIVATED_BY",
            "REQUIRES",
            "BLOCKS",
            "FALLBACK_TO",
            "ISSUED_BY",
        ):
            self.assertIn(kind, kinds, f"missing edge kind {kind}")

        # purchase root exists and entitlements hang off it
        assert any(n["type"] == "purchase" for n in nodes)
        ent_nodes = [n for n in nodes if n["type"] == "entitlement"]
        self.assertEqual(len(ent_nodes), 5)
        for n in ent_nodes:
            self.assertTrue(
                any(
                    e["kind"] == "ISSUED_BY" and e["source"] == n["id"]
                    for e in edges
                ),
                f"{n['id']} missing ISSUED_BY edge",
            )

        # breach carries promised-vs-observed on its MATERIAL_TO edge
        breach_node = next(n for n in nodes if n["type"] == "breach")
        m_edge = next(
            e
            for e in edges
            if e["kind"] == "MATERIAL_TO" and e["target"] == breach_node["id"]
        )
        self.assertEqual(m_edge["promised"], "manufacturer")
        self.assertEqual(m_edge["observed"], "seller")

        # evidence nodes carry hashes for audit UI
        ev_node = next(n for n in nodes if n["type"] == "evidence")
        self.assertTrue(ev_node.get("sha256"))
        self.assertTrue(ev_node.get("synthetic"))
        _ = seeded

    def test_edges_are_deduplicated(self):
        _seed_hero_breach()
        g1 = build_rights_graph("con_test_1")
        keys1 = [(e["source"], e["target"], e["kind"]) for e in g1["edges"]]
        self.assertEqual(len(keys1), len(set(keys1)))


if __name__ == "__main__":
    unittest.main()

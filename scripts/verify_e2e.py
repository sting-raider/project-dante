"""Project Dante - end-to-end hero-flow verification script.

Drives the full arc against a running API (default http://localhost:8000):

    reset -> compile intent -> search -> select offer -> authorize ->
    payment order -> [sandbox: simulate captured webhook | live: wait] ->
    ship -> deliver WRONG VARIANT -> breach detected -> rights graph ->
    remedies planned (replacement blocked) -> policy ALLOW -> refund executed ->
    contract REMEDIATED

Usage:
    python scripts/verify_e2e.py [--api http://localhost:8000]

Exit code 0 = full arc verified; non-zero = first failure point printed.
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any

import httpx

HERO_TEXT = (
    "Buy me over-ear ANC headphones under Rs 12,000. I need an Indian manufacturer "
    "warranty, they must arrive by Thursday, and do not spend over Rs 12,000."
)

STEPS: list[tuple[str, str]] = []


def step(name: str, detail: str = "") -> None:
    STEPS.append((name, detail))
    print(f"[{len(STEPS):02d}] {name}" + (f" -- {detail}" if detail else ""))


def fail(msg: str) -> None:
    last = STEPS[-1][0] if STEPS else "?"
    print(f"\nFAIL at step '{last}': {msg}")
    sys.exit(1)


def expect(cond: Any, msg: str) -> None:
    if not cond:
        fail(msg)


def poll_status(
    c: httpx.Client, contract_id: str, targets: set[str], timeout_s: float = 15.0
) -> dict:
    deadline = time.time() + timeout_s
    last: dict = {}
    while time.time() < deadline:
        r = c.get(f"/api/contracts/{contract_id}")
        if r.status_code == 200:
            last = r.json().get("contract", {})
            if last.get("status") in targets:
                return last
        time.sleep(0.5)
    fail(f"timed out waiting for status in {sorted(targets)}; last={last.get('status')}")
    return {}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    args = ap.parse_args()

    c = httpx.Client(base_url=args.api.rstrip("/"), timeout=30)

    r = c.get("/api/health")
    expect(r.status_code == 200, f"health check failed: {r.status_code}")
    mode = r.json().get("razorpay")
    step("health", f"razorpay={mode}")

    r = c.post("/api/demo/reset")
    if r.status_code == 403:
        fail("DEMO_MODE is off; e2e needs demo endpoints")
    expect(r.status_code == 200, f"demo reset failed: {r.status_code} {r.text[:200]}")
    step("reset", f"products={r.json().get('products')}")

    r = c.post("/api/intents/compile", json={"raw_text": HERO_TEXT})
    expect(r.status_code == 200, f"compile failed: {r.status_code} {r.text[:300]}")
    intent = r.json()["intent"]
    hc = {cst["key"]: cst for cst in intent.get("hard_constraints", [])}
    for key in ("max_price_paise", "warranty.type"):
        expect(key in hc, f"intent missing hard constraint {key}: got {sorted(hc)}")
    step("compile", f"engine={r.json().get('engine')} constraints={len(hc)}")

    iid = intent["id"]
    r = c.post(f"/api/intents/{iid}/search")
    expect(r.status_code == 200, f"search failed: {r.status_code} {r.text[:300]}")
    results = r.json()["results"]
    feasible = [x for x in results if x["evaluation"]["feasible"]]
    expect(feasible, "no feasible offers found")
    hero = next((x for x in feasible if x["offer"]["sku"] == "AST-HP-ANC-001"), feasible[0])
    step("search", f"{len(results)} results, {len(feasible)} feasible; sku={hero['offer']['sku']}")

    r = c.post(f"/api/intents/{iid}/select-offer", json={"offer_id": hero["offer"]["id"]})
    expect(r.status_code == 200, f"select-offer failed: {r.status_code} {r.text[:300]}")
    body = r.json()
    contract = body["contract"]
    cid = contract["id"]
    n_promises = len(body.get("promises", []))
    expect(n_promises >= 5, f"frozen promises too few: {n_promises}")
    expect(contract.get("promise_set_hash"), "promise_set_hash missing")
    step("freeze", f"contract={cid} status={contract['status']} promises={n_promises}")

    r = c.post(f"/api/contracts/{cid}/authorize", json={})
    expect(r.status_code == 200, f"authorize failed: {r.status_code} {r.text[:300]}")
    chash = contract.get("contract_hash") or ""
    step("authorize", f"hash={chash[:12]}")

    r = c.post(f"/api/contracts/{cid}/payment-order", json={})
    expect(r.status_code == 200, f"payment-order failed: {r.status_code} {r.text[:300]}")
    po = r.json()
    order_id = po["checkout_config"]["order_id"]
    step("order", f"mode={po['mode']} order={order_id}")

    if po["mode"] == "sandbox":
        r = c.post(
            "/api/demo/razorpay/simulate-event",
            json={"event_type": "payment.captured", "order_id": order_id},
        )
        expect(r.status_code == 200, f"simulate-event failed: {r.status_code} {r.text[:200]}")
        final = poll_status(c, cid, {"PAID"})
        pay = final.get("razorpay_payment_id")
        step("payment", f"PAID via signed sandbox webhook; payment={pay}")
    else:
        print("LIVE TEST MODE: complete the Razorpay Checkout payment in the browser;")
        print("waiting up to 120s for the webhook to flip PAID...")
        final = poll_status(c, cid, {"PAID"}, timeout_s=120.0)
        pay = final.get("razorpay_payment_id")
        step("payment", f"PAID via real webhook; payment={pay}")

    r = c.post(f"/api/demo/contracts/{cid}/ship", json={})
    expect(r.status_code == 200, f"ship failed: {r.status_code} {r.text[:200]}")
    r = c.post(f"/api/demo/contracts/{cid}/replacement-unavailable", json={})
    expect(r.status_code in (200, 404), "replacement-unavailable call errored")
    r = c.post(f"/api/demo/contracts/{cid}/deliver", json={"scenario": "wrong_variant"})
    expect(r.status_code == 200, f"deliver failed: {r.status_code} {r.text[:200]}")
    d = r.json()
    breaches = d.get("breaches", [])
    expect(breaches, "expected a breach on wrong_variant delivery")
    codes = [b.get("reason_code") for b in breaches]
    step("breach", f"reasons={codes} status={d.get('status') or d.get('contract_status')}")

    r = c.get(f"/api/contracts/{cid}/rights")
    expect(r.status_code == 200, f"rights failed: {r.status_code}")
    graph = r.json()["graph"]
    ents = r.json().get("entitlements", [])
    blocked = [e for e in ents if e.get("status") == "blocked"]
    eligible = [e for e in ents if e.get("status") == "eligible"]
    expect(graph.get("nodes"), "empty rights graph")
    expect(eligible, f"no eligible entitlements; statuses={[e.get('status') for e in ents]}")
    step(
        "rights",
        f"nodes={len(graph['nodes'])} edges={len(graph.get('edges', []))} "
        f"eligible={len(eligible)} blocked={len(blocked)}",
    )

    r = c.get(f"/api/contracts/{cid}/remedies")
    expect(r.status_code == 200, f"remedies failed: {r.status_code}")
    props = r.json()["proposals"]
    chosen = next((p for p in props if p.get("rejected_reason") is None), None)
    expect(chosen, "no chosen remedy proposal")
    expect(
        chosen["remedy_type"] == "refund_full",
        f"expected refund_full as chosen remedy, got {chosen['remedy_type']}",
    )
    rejected = [(p["remedy_type"], p["rejected_reason"]) for p in props if p.get("rejected_reason")]
    step("remedy", f"chosen={chosen['remedy_type']} rejected={rejected}")

    rid = chosen["id"]
    r = c.post(f"/api/remedies/{rid}/policy")
    expect(r.status_code == 200, f"policy eval failed: {r.status_code} {r.text[:200]}")
    decision = r.json()["decision"]
    expect(decision["decision"] == "ALLOW", f"expected ALLOW, got {decision['decision']}: {decision}")
    step("policy", f"{decision['decision']} policies={decision.get('policy_ids')}")

    r = c.post(f"/api/remedies/{rid}/execute", json={})
    expect(r.status_code == 200, f"execute failed: {r.status_code} {r.text[:300]}")
    ex = r.json()
    expect(ex.get("executed") is True, f"not executed: {ex}")
    refund_ref = (ex.get("money_action") or {}).get("result_ref")
    expect(refund_ref, "refund id missing from execution result")
    step("refund", f"idempotent refund executed: {refund_ref}")

    r2 = c.post(f"/api/remedies/{rid}/execute", json={})
    expect(r2.status_code == 200, "second execute errored")
    ref2 = (r2.json().get("money_action") or {}).get("result_ref")
    expect(ref2 == refund_ref, f"double execute produced different refund ids: {refund_ref} vs {ref2}")
    step("idempotency", "repeat execute returned same refund id (one money effect)")

    final = poll_status(c, cid, {"REMEDIATED"})
    step("terminal", f"contract={final['status']}")

    r = c.get(f"/api/contracts/{cid}/timeline")
    expect(r.status_code == 200, "timeline fetch failed")
    events = r.json().get("events", [])
    etypes = [e.get("event_type") for e in events]
    needed = {
        "INTENT_COMPILED",
        "CONTRACT_CREATED",
        "BUYER_AUTHORIZED",
        "RAZORPAY_ORDER_CREATED",
        "RAZORPAY_PAYMENT_CAPTURED",
        "PROMISE_BREACH_DETECTED",
        "POLICY_ALLOWED",
        "REFUND_PROCESSED",
    }
    missing = needed - set(etypes)
    expect(not missing, f"audit trail missing events: {missing}")
    synth = [e for e in events if e.get("synthetic")]
    step("audit", f"{len(events)} events, {len(synth)} synthetic-labeled, all key events present")

    print("")
    print("E2E VERIFICATION PASSED")
    print("intent -> payment -> breach -> rights -> policy -> refund -> audit")


if __name__ == "__main__":
    main()

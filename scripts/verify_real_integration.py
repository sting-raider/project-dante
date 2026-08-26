"""Project Dante - REAL-INTEGRATION evidence collector (requirements 5 + 7).

Unlike scripts/verify_e2e.py (which passes happily against the offline
sandbox adapter), this script refuses to run unless the API is genuinely in
razorpay_mode == 'live-test-mode' (real rzp_test_* keys configured). Its job
is to turn requirement 5's checklist from NOT_YET_PROVEN into PROVEN, with
real gateway ids as evidence, appended to REAL_INTEGRATION_STATUS.md.

Flow (money actions are REAL Test Mode - small real amounts, test cards):

    preflight (mode + webhook secret) -> reset -> compile -> search ->
    select offer -> authorize -> payment-order  [prints REAL order_...] ->
    opens the buyer contract page in a browser; waits up to 180 s polling
    contract status every 2 s for the human to finish the REAL Standard
    Checkout payment -> on PAID prints the REAL pay_... id and the verified
    webhook evidence from the timeline -> ship -> deliver WRONG VARIANT
    (X-Demo-Operator-Token from $DEMO_OPERATOR_TOKEN) -> breach detected ->
    rights graph -> remedies planned -> policy ALLOW -> execute [prints REAL
    rf_... refund id] -> repeat execute [asserts IDENTICAL refund id] ->
    REMEDIATED + audit trail.

Every step appends a timestamped evidence line to REAL_INTEGRATION_STATUS.md
between BEGIN-RUN/END-RUN markers. Exit code 0 only when ALL criteria pass.

Usage:
    python scripts/verify_real_integration.py [--api http://localhost:8000]
        [--web http://localhost:3000] [--wait 180]

Environment:
    DEMO_OPERATOR_TOKEN   sent as X-Demo-Operator-Token on every /api/demo/*
                          call (the live-mode hybrid-demo gate).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import webbrowser
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
APPS_API = REPO_ROOT / "apps" / "api"
STATUS_FILE = REPO_ROOT / "REAL_INTEGRATION_STATUS.md"

HERO_TEXT = (
    "Buy me over-ear ANC headphones under Rs 12,000. I need an Indian manufacturer "
    "warranty, they must arrive within 3 days, and do not spend over Rs 12,000."
)

# Requirement 5 checklist, mirrored one-to-one in REAL_INTEGRATION_STATUS.md.
CRITERIA: list[tuple[str, str]] = [
    ("order", "real order created: Razorpay order_... id minted in live-test-mode"),
    ("paid", "real payment captured: Razorpay pay_... id bound to the contract"),
    ("webhook", "webhook received + signature-verified (raw-body HMAC BEFORE parse)"),
    ("paid_from_webhook", "PAID granted by the webhook path only (no client-verify shortcut)"),
    ("wrong_variant", "synthetic wrong-variant delivery applied with operator token"),
    ("breach", "promise breach detected from the wrong-variant fact"),
    ("rights", "rights graph built with eligible entitlements"),
    ("remedy", "remedy planned: refund_full chosen, policy decision ALLOW"),
    ("refund", "real refund executed: Razorpay rf_... id returned"),
    ("idempotent", "repeat execute returns the SAME refund id - no second refund"),
]


class Fail(Exception):
    """Raised by expect(); carries the message printed at the failure point."""


def now_local() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


class Evidence:
    """Appends timestamped evidence lines between marked run blocks."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.stamp = now_local().replace(":", "-")
        self.results: dict[str, tuple[str, str]] = {}  # id -> (PASS/FAIL, detail)

    def _append(self, text: str) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(text.rstrip("\n") + "\n")

    def begin(self) -> None:
        self._append("")
        self._append(f"<!-- BEGIN-RUN {self.stamp} -->")
        self._append(f"- RUN STARTED: {now_local()} (script: scripts/verify_real_integration.py)")

    def log(self, msg: str) -> None:
        line = f"  - {now_local()} {msg}"
        print(line.strip())
        self._append(line)

    def criterion(self, cid: str, ok: bool, detail: str) -> None:
        label = "PROVEN" if ok else "FAILED"
        self.results[cid] = ("PROVEN" if ok else "FAILED", detail)
        name = next(d for k, d in CRITERIA if k == cid)
        self.log(f"[criterion:{cid}] {label} -- {name} :: {detail}")

    def end(self, ok: bool, fail_msg: str = "") -> bool:
        self._append("")
        self._append("  Criteria summary for this run:")
        self._append("  | Criterion | Result | Evidence |")
        self._append("  | --- | --- | --- |")
        for cid, req in CRITERIA:
            res, det = self.results.get(cid, ("NOT_RUN", "-"))
            safe_det = det.replace("|", "/")
            self._append(f"  | {cid} ({req}) | {res} | {safe_det} |")
        if ok:
            verdict = "PASSED - ALL REQUIREMENT-5 CRITERIA PROVEN AGAINST REAL RAZORPAY TEST MODE"
        else:
            verdict = f"FAILED - {fail_msg}" if fail_msg else "FAILED"
        self._append(f"- RUN RESULT: {verdict}")
        self._append(f"- RUN ENDED: {now_local()}")
        self._append(f"<!-- END-RUN {self.stamp} -->")
        return ok


EV: Evidence | None = None


def log(msg: str) -> None:
    if EV is not None:
        EV.log(msg)
    else:
        print(f"  - {msg}")


def expect(cond: Any, msg: str) -> None:
    if not cond:
        raise Fail(msg)


def operator_headers() -> dict[str, str]:
    tok = os.environ.get("DEMO_OPERATOR_TOKEN", "")
    return {"X-Demo-Operator-Token": tok} if tok else {}


def load_settings_or_exit() -> Any:
    """Read the SAME settings the API would read (root .env + apps/api/.env)."""
    sys.path.insert(0, str(APPS_API))
    try:
        from project_dante.settings import get_settings
    except Exception as exc:  # pragma: no cover - broken install
        print(f"Cannot import project_dante.settings ({exc}); "
              f"cannot confirm razorpay_mode. Run from the repo root.")
        sys.exit(1)
    try:
        return get_settings()
    except Exception as exc:
        # Includes LiveKeyRejected: rzp_live_* credentials fail closed here too.
        print(f"Settings rejected: {exc}")
        sys.exit(1)


def preflight_settings(settings: Any) -> None:
    mode = settings.razorpay_mode
    if mode != "live-test-mode":
        print()
        print("REAL-INTEGRATION VERIFY REFUSED.")
        print(f"razorpay_mode = '{mode}' (sandbox adapter). This script exists to prove")
        print("requirement 5 against the REAL gateway; the sandbox already passes e2e.")
        print("Set rzp_test_ keys: RAZORPAY_KEY_ID (rzp_test_...) + RAZORPAY_KEY_SECRET")
        print("in .env (repo root) or apps/api/.env, plus a non-default")
        print("RAZORPAY_WEBHOOK_SECRET, then restart the API and rerun this script.")
        print("See REAL_INTEGRATION_STATUS.md section 'Where the keys go'.")
        sys.exit(1)
    log(f"preflight: razorpay_mode='{mode}' (real Test Mode keys configured)")

    secret = (settings.razorpay_webhook_secret or "").strip()
    if not secret or secret == "dante-dev-webhook-secret":
        print()
        print("REAL-INTEGRATION VERIFY REFUSED (before any money moved).")
        print("RAZORPAY_WEBHOOK_SECRET is missing or still the repo default. In")
        print("live-test-mode the webhook gate fails CLOSED, so a real payment would")
        print("be captured by Razorpay but NEVER flip the contract to PAID. Set the")
        print("dashboard webhook secret in .env and restart the API first.")
        sys.exit(1)
    log("preflight: RAZORPAY_WEBHOOK_SECRET is configured (non-default)")


def main() -> None:
    global EV

    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--web", default="", help="buyer web app base URL (default: PUBLIC_APP_URL)")
    ap.add_argument("--wait", type=float, default=180.0, help="seconds to wait for the payment")
    args = ap.parse_args()

    settings = load_settings_or_exit()
    preflight_settings(settings)

    c = httpx.Client(
        base_url=args.api.rstrip("/"), timeout=30, headers=operator_headers(),
    )
    web_base = (args.web or settings.public_app_url or "http://localhost:3000").rstrip("/")

    if not os.environ.get("DEMO_OPERATOR_TOKEN"):
        log("WARNING: DEMO_OPERATOR_TOKEN is not set; the live-mode demo fulfillment")
        log("endpoints may answer 403. Export DEMO_OPERATOR_TOKEN and rerun if so.")

    EV = Evidence(STATUS_FILE)
    if not STATUS_FILE.exists():
        STATUS_FILE.write_text("", encoding="utf-8")
    EV.begin()

    fail_msg = ""
    ok = False
    try:
        ok = run_flow(c, web_base, args.wait)
    except Fail as exc:
        fail_msg = str(exc)
        log(f"FAIL: {fail_msg}")
    except Exception as exc:  # noqa: BLE001 - evidence must survive any crash
        fail_msg = f"unexpected error: {exc!r}"
        log(f"FAIL: {fail_msg}")
    finally:
        all_proven = ok and all(
            EV.results.get(cid, ("NOT_RUN", ""))[0] == "PROVEN" for cid, _ in CRITERIA
        )
        EV.end(all_proven, fail_msg)

    print()
    if all_proven:
        print("REAL-INTEGRATION VERIFICATION PASSED")
        print("all requirement-5 criteria proven with real gateway ids; see "
              "REAL_INTEGRATION_STATUS.md")
        sys.exit(0)
    print(f"REAL-INTEGRATION VERIFICATION FAILED{(': ' + fail_msg) if fail_msg else ''}")
    print("evidence for the aborted run was appended to REAL_INTEGRATION_STATUS.md")
    sys.exit(1)


def run_flow(c: httpx.Client, web_base: str, wait_s: float) -> bool:
    # ---- 0. server truth ----------------------------------------------------
    r = c.get("/api/health")
    expect(r.status_code == 200, f"health check failed: {r.status_code} - is the API running?")
    health = r.json()
    expect(
        health.get("razorpay") == "live-test-mode",
        f"running API reports razorpay='{health.get('razorpay')}', not live-test-mode; "
        "restart the API with the same .env this script read",
    )
    log(f"health: api={health.get('service')} razorpay={health.get('razorpay')} "
        f"llm_engine={health.get('llm_engine')}")

    r = c.post("/api/demo/reset")
    if r.status_code == 403:
        fail_msg = (
            "demo reset refused (403): live-mode demo endpoints require a valid "
            "X-Demo-Operator-Token - export DEMO_OPERATOR_TOKEN matching the server "
            "configuration and rerun"
        )
        expect(False, fail_msg)
    expect(r.status_code == 200, f"demo reset failed: {r.status_code} {r.text[:200]}")
    log(f"reset: products={r.json().get('products')} (clean store for unambiguous evidence)")

    # ---- 1-3. intent -> search -> freeze -------------------------------------
    r = c.post("/api/intents/compile", json={"raw_text": HERO_TEXT})
    expect(r.status_code == 200, f"compile failed: {r.status_code} {r.text[:300]}")
    intent = r.json()["intent"]
    engine = r.json().get("engine")
    iid = intent["id"]
    log(f"compile: intent={iid} engine={engine} "
        f"hard_constraints={len(intent.get('hard_constraints', []))} (LLM never executes money)")

    r = c.post(f"/api/intents/{iid}/search")
    expect(r.status_code == 200, f"search failed: {r.status_code} {r.text[:300]}")
    results = r.json()["results"]
    feasible = [x for x in results if x["evaluation"]["feasible"]]
    expect(feasible, "no feasible offers found")
    hero = next((x for x in feasible if x["offer"]["sku"] == "AST-HP-ANC-001"), feasible[0])
    raw_amount = (
        hero["evaluation"].get("total_paise")
        or hero["offer"].get("unit_amount_paise")
        or hero["offer"].get("price_paise")
    )
    expect(raw_amount is not None, "no price field on chosen offer")
    amount_paise = int(raw_amount)
    log(f"search: {len(results)} results, {len(feasible)} feasible; sku={hero['offer']['sku']} "
        f"amount_paise={amount_paise}")

    r = c.post(f"/api/intents/{iid}/select-offer", json={"offer_id": hero["offer"]["id"]})
    expect(r.status_code == 200, f"select-offer failed: {r.status_code} {r.text[:300]}")
    body = r.json()
    contract = body["contract"]
    cid = contract["id"]
    n_promises = len(body.get("promises", []))
    expect(n_promises >= 5, f"frozen promises too few: {n_promises}")
    expect(contract.get("promise_set_hash"), "promise_set_hash missing")
    log(f"freeze: contract={cid} promises={n_promises} "
        f"psh={str(contract.get('promise_set_hash'))[:12]}")

    # ---- 4. authorize ---------------------------------------------------------
    r = c.post(f"/api/contracts/{cid}/authorize", json={})
    expect(r.status_code == 200, f"authorize failed: {r.status_code} {r.text[:300]}")
    log(f"authorize: hash={str(contract.get('contract_hash'))[:12]} scope=single_purchase")

    # ---- 5. REAL payment order -------------------------------------------------
    r = c.post(f"/api/contracts/{cid}/payment-order", json={})
    expect(r.status_code == 200, f"payment-order failed: {r.status_code} {r.text[:300]}")
    po = r.json()
    order_id = po["checkout_config"]["order_id"]
    key_id = po["checkout_config"]["key_id"]
    expect(
        po["mode"] == "live-test-mode",
        f"payment-order mode={po['mode']} (wanted live-test-mode)",
    )
    expect(order_id.startswith("order_"), f"order id not Razorpay-shaped: {order_id}")
    expect(key_id.startswith("rzp_test_"), f"checkout key_id not rzp_test_*: '{key_id}'")
    EV.criterion("order", True, f"real Razorpay order id {order_id} (amount {amount_paise} paise, "
                                f"checkout key {key_id})")
    log(f"ORDER (REAL): {order_id}")

    # ---- 6. human completes the REAL Standard Checkout payment -----------------
    pay_url = f"{web_base}/contract/{cid}"
    print()
    print("=" * 78)
    print("ACTION REQUIRED (human): complete the REAL Razorpay Standard Checkout")
    print(f"  Open: {pay_url}")
    print(f"  Order: {order_id}  Amount: {amount_paise} paise")
    print("  (use a TEST card, e.g. 4111 1111 1111 1111 - never a real card)")
    print(f"  Waiting up to {int(wait_s)}s for the signature-verified webhook to grant PAID...")
    print("=" * 78)
    try:
        opened = webbrowser.open(pay_url)
    except Exception:  # pragma: no cover - headless hosts
        opened = False
    log(f"checkout: browser {'opened' if opened else 'NOT opened (headless?)'} at {pay_url}")

    deadline = time.time() + wait_s
    final: dict = {}
    last_status = ""
    while time.time() < deadline:
        rr = c.get(f"/api/contracts/{cid}")
        expect(rr.status_code == 200, f"contract poll failed: {rr.status_code}")
        final = rr.json().get("contract", {})
        last_status = str(final.get("status"))
        if last_status == "PAID":
            break
        time.sleep(2.0)
    expect(
        last_status == "PAID",
        f"timed out after {int(wait_s)}s waiting for PAID (last={last_status}). Check: "
        "was the Checkout payment completed? Is the Razorpay dashboard webhook pointed at "
        "<public-api>/api/webhooks/razorpay with the SAME secret (localhost needs a tunnel)?",
    )
    pay_id = str(final.get("razorpay_payment_id") or "")
    expect(pay_id.startswith("pay_"), f"payment id missing/not Razorpay-shaped: '{pay_id}'")
    expect(not final.get("sandbox_mode"), "contract flagged sandbox_mode - not a real payment")
    EV.criterion("paid", True, f"real Razorpay payment id {pay_id} captured on order {order_id}")
    log(f"PAYMENT (REAL): {pay_id}")

    # ---- 7. webhook received + verified, and PAID came from the webhook --------
    r = c.get(f"/api/contracts/{cid}/timeline")
    expect(r.status_code == 200, f"timeline fetch failed: {r.status_code}")
    events = r.json().get("events", [])
    etypes = [e.get("event_type") for e in events]
    expect("RAZORPAY_PAYMENT_CAPTURED" in etypes,
           "timeline lacks RAZORPAY_PAYMENT_CAPTURED (the webhook handler's effect)")
    capture_events = [e for e in events if e.get("event_type") == "RAZORPAY_PAYMENT_CAPTURED"]
    hook_ids = []
    for e in events:
        p = e.get("payload") or {}
        for k in ("webhook_event_id", "event_id"):
            v = p.get(k)
            if isinstance(v, str) and (v.startswith("evt_") or v.startswith("sha256_")):
                hook_ids.append((e.get("event_type"), v))
    hook_note = "; ".join(f"{t}:{i}" for t, i in hook_ids) if hook_ids else (
        "provider event id not surfaced on contract timeline; verification is structural: "
        "this script never called /verify-client or /simulate-event, and routes/webhooks.py "
        "is the ONLY code path that grants PAID, behind raw-body HMAC verification"
    )
    EV.criterion("webhook", True, f"verified webhook processed: {len(capture_events)} capture "
                                  f"event(s) on timeline; {hook_note}")
    log(f"WEBHOOK: verified capture processed; evidence={hook_note or 'RAZORPAY_PAYMENT_CAPTURED'}")

    client_paths_used = [
        t for t in ("CHECKOUT_COMPLETED_CLIENT", "PAYMENT_VERIFIED_SERVER") if t in etypes
    ]
    expect(
        not client_paths_used,
        f"client-verify path events found ({client_paths_used}) - PAID must come from the webhook",
    )
    EV.criterion("paid_from_webhook", True,
                 "contract reached PAID exclusively via signature-verified webhook intake "
                 "(no CHECKOUT_COMPLETED_CLIENT/PAYMENT_VERIFIED_SERVER events exist)")
    log("PAID-FROM-WEBHOOK: proven structurally (client-verify paths unused)")

    # ---- 8-9. synthetic wrong-variant fulfillment + breach ----------------------
    r = c.post(f"/api/demo/contracts/{cid}/ship", json={})
    expect(r.status_code == 200, f"ship failed: {r.status_code} {r.text[:200]} "
                                 "(is X-Demo-Operator-Token accepted by the server gate?)")
    r = c.post(f"/api/demo/contracts/{cid}/replacement-unavailable", json={})
    expect(r.status_code in (200, 404), "replacement-unavailable call errored")
    r = c.post(f"/api/demo/contracts/{cid}/deliver", json={"scenario": "wrong_variant"})
    expect(r.status_code == 200, f"deliver(wrong_variant) failed: {r.status_code} {r.text[:200]}")
    d = r.json()
    expect(d.get("synthetic") is True, "delivery response missing synthetic marker")
    EV.criterion("wrong_variant", True,
                 "synthetic wrong_variant delivery applied via /demo/deliver with "
                 "X-Demo-Operator-Token (response synthetic=true)")
    log("DELIVERY: wrong_variant (synthetic, operator-token gated)")

    breaches = d.get("breaches", [])
    codes = [b.get("reason_code") or b.get("key") or b.get("promise_key") for b in breaches]
    expect(breaches, f"expected a breach on wrong_variant delivery; got {d}")
    EV.criterion("breach", True, f"PROMISE_BREACH_DETECTED reasons={codes}")
    log(f"BREACH: reasons={codes}")

    # ---- 10. rights --------------------------------------------------------------
    r = c.get(f"/api/contracts/{cid}/rights")
    expect(r.status_code == 200, f"rights failed: {r.status_code}")
    graph = r.json()["graph"]
    ents = r.json().get("entitlements", [])
    eligible = [e for e in ents if e.get("status") == "eligible"]
    blocked = [e for e in ents if e.get("status") == "blocked"]
    expect(graph.get("nodes"), "empty rights graph")
    expect(eligible, f"no eligible entitlements; statuses={[e.get('status') for e in ents]}")
    EV.criterion("rights", True, f"rights graph nodes={len(graph['nodes'])} "
                                 f"edges={len(graph.get('edges', []))} eligible={len(eligible)} "
                                 f"blocked={len(blocked)}")
    log(f"RIGHTS: nodes={len(graph['nodes'])} eligible={len(eligible)} blocked={len(blocked)}")

    # ---- 11. remedy plan + policy --------------------------------------------------
    r = c.get(f"/api/contracts/{cid}/remedies")
    expect(r.status_code == 200, f"remedies failed: {r.status_code}")
    props = r.json()["proposals"]
    chosen = next((p for p in props if p.get("rejected_reason") is None), None)
    expect(chosen, "no chosen remedy proposal")
    expect(chosen["remedy_type"] == "refund_full",
           f"expected refund_full as chosen remedy, got {chosen['remedy_type']}")
    rid = chosen["id"]
    r = c.post(f"/api/remedies/{rid}/policy")
    expect(r.status_code == 200, f"policy eval failed: {r.status_code} {r.text[:200]}")
    decision = r.json()["decision"]
    expect(decision["decision"] == "ALLOW",
           f"expected ALLOW, got {decision['decision']}: {decision}")
    EV.criterion("remedy", True, f"proposal {rid} refund_full chosen; policy ALLOW "
                                 f"policies={decision.get('policy_ids')}")
    log(f"REMEDY: {chosen['remedy_type']} proposal={rid}; POLICY: ALLOW")

    # ---- 12. REAL refund -------------------------------------------------------------
    r = c.post(f"/api/remedies/{rid}/execute", json={})
    expect(r.status_code == 200, f"execute failed: {r.status_code} {r.text[:300]}")
    ex = r.json()
    expect(ex.get("executed") is True, f"not executed: {ex}")
    ma = ex.get("money_action") or {}
    refund_ref = ma.get("result_ref")
    expect(isinstance(refund_ref, str) and refund_ref.startswith("rf_"),
           f"refund id missing/not Razorpay rf_-shaped: {refund_ref!r}")
    EV.criterion("refund", True, f"real Razorpay refund id {refund_ref} "
                                 f"(money_action={ma.get('id')})")
    log(f"REFUND (REAL): {refund_ref}")

    # ---- 13. repeat execute => same refund, no second money effect ---------------------
    r2 = c.post(f"/api/remedies/{rid}/execute", json={})
    expect(r2.status_code == 200, f"second execute errored: {r2.status_code} {r2.text[:200]}")
    ex2 = r2.json()
    ma2 = ex2.get("money_action") or {}
    ref2 = ma2.get("result_ref")
    expect(
        ref2 == refund_ref,
        f"double execute produced different refund ids: {refund_ref} vs {ref2}",
    )
    expect(ma2.get("id") == ma.get("id"),
           f"double execute produced a second money action: {ma.get('id')} vs {ma2.get('id')}")
    EV.criterion("idempotent", True, f"repeat execute returned the SAME refund id {refund_ref} "
                                     f"(same money_action {ma.get('id')}; single money effect)")
    log(f"IDEMPOTENCY: repeat execute -> same refund {refund_ref}")

    # ---- 14. terminal state + audit ------------------------------------------------------
    deadline = time.time() + 20
    term = ""
    while time.time() < deadline:
        rr = c.get(f"/api/contracts/{cid}")
        term = str(rr.json().get("contract", {}).get("status"))
        if term == "REMEDIATED":
            break
        time.sleep(0.5)
    expect(term == "REMEDIATED", f"contract never reached REMEDIATED (last={term})")

    # Re-fetch the timeline AFTER the refund so the audit check sees the full arc.
    r = c.get(f"/api/contracts/{cid}/timeline")
    expect(r.status_code == 200, "final timeline fetch failed")
    final_events = r.json().get("events", [])
    etypes_final = [e.get("event_type") for e in final_events]
    needed = {
        "INTENT_COMPILED", "CONTRACT_CREATED", "BUYER_AUTHORIZED", "RAZORPAY_ORDER_CREATED",
        "RAZORPAY_PAYMENT_CAPTURED", "PROMISE_BREACH_DETECTED", "POLICY_ALLOWED",
        "REFUND_PROCESSED",
    }
    missing = sorted(needed - set(etypes_final))
    expect(not missing, f"audit trail missing events: {missing}")
    synth = [e for e in final_events if e.get("synthetic")]
    log(f"AUDIT: {len(final_events)} timeline events, {len(synth)} synthetic-labeled, "
        f"all key events present, terminal={term}")
    return True


if __name__ == "__main__":
    main()

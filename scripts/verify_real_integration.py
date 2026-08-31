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
    rfnd_... refund id] -> repeat execute [asserts IDENTICAL refund id] ->
    REMEDIATED + audit trail.

Every step appends a timestamped evidence line to REAL_INTEGRATION_STATUS.md
between BEGIN-RUN/END-RUN markers. Exit code 0 only when ALL criteria pass.

Usage:
    python scripts/verify_real_integration.py [--api http://localhost:8000]
        [--web http://localhost:3000] [--wait 180] [--no-open]
    python scripts/verify_real_integration.py --resume-contract con_...
        [--api http://localhost:8000] [--web http://localhost:3000] [--wait 600] [--no-open]

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

BUNDLE_TEXT = (
    "Buy me a 27-inch QHD monitor under ₹25,000 and a mechanical keyboard under "
    "₹8,000. The monitor must have an IPS panel, at least a 144 Hz refresh rate, "
    "DisplayPort, and an Indian manufacturer warranty. The keyboard should be 75% "
    "or TKL, hot-swappable, wireless, and also have an Indian manufacturer warranty. "
    "I prefer tactile switches, but linear switches are acceptable. Both items must "
    "arrive within 5 days. Do not show me any monitor over ₹25,000 or any keyboard "
    "over ₹8,000. Keep the total order under ₹33,000."
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
    ("refund", "real refund executed: Razorpay rfnd_... id returned"),
    ("idempotent", "repeat execute returns the SAME refund id - no second refund"),
    ("llm_basket", "real LLM compiled the exact two-line monitor + keyboard basket"),
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

    def promote_checklist(self) -> None:
        """Promote the human-readable ledger after a complete real run.

        Run blocks are append-only evidence, while the ten-row checklist is a
        current projection of the strongest successful run. Keep the
        projection fail-closed: a partial or failed run cannot mark anything
        proven, and a later failed run does not erase an earlier success.
        """
        incomplete = [
            cid for cid, _ in CRITERIA
            if self.results.get(cid, ("NOT_RUN", ""))[0] != "PROVEN"
        ]
        if incomplete:
            raise ValueError(
                "cannot promote incomplete real-integration checklist: "
                + ", ".join(incomplete)
            )

        lines = self.path.read_text(encoding="utf-8").splitlines()
        missing_rows: list[str] = []
        for row_number, (cid, _) in enumerate(CRITERIA, start=1):
            row_prefix = f"| {row_number} |"
            row_index = next(
                (i for i, line in enumerate(lines) if line.startswith(row_prefix)),
                None,
            )
            if row_index is None:
                missing_rows.append(str(row_number))
                continue

            cells = lines[row_index].split("|")
            if len(cells) < 7:
                missing_rows.append(str(row_number))
                continue
            detail = self.results[cid][1].replace("|", "/").replace("\n", " ").strip()
            cells[3] = " `PROVEN` "
            cells[5] = f" {detail} "
            lines[row_index] = "|".join(cells)

        if missing_rows:
            raise ValueError(
                "cannot promote real-integration checklist; malformed/missing rows: "
                + ", ".join(missing_rows)
            )

        self.path.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")

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


def redact_checkout_key_id(key_id: str) -> str:
    """Keep provider key identifiers out of durable evidence and logs."""
    if key_id.startswith("rzp_test_"):
        return "rzp_test_<redacted>"
    if key_id.startswith("rzp_live_"):
        return "rzp_live_<redacted>"
    return "<redacted>"


def expect(cond: Any, msg: str) -> None:
    if not cond:
        raise Fail(msg)


def operator_headers(settings: Any | None = None) -> dict[str, str]:
    """Use the process environment first, then the API's dotenv-backed settings."""
    tok = os.environ.get("DEMO_OPERATOR_TOKEN", "")
    if not tok and settings is not None:
        tok = str(getattr(settings, "demo_operator_token", "") or "")
    tok = tok.strip()
    return {"X-Demo-Operator-Token": tok} if tok else {}


def record_llm_basket_proof(
    c: httpx.Client,
    contract_id: str,
    contract: dict[str, Any],
) -> None:
    """Prove the final basket used the LLM compiler, from persisted evidence.

    The response from ``/compile`` is not enough: this proof reads the
    contract's canonical timeline and frozen line items, so a UI label or a
    transient provider response cannot make the run claim LLM execution.
    """
    r = c.get(f"/api/contracts/{contract_id}/timeline")
    expect(r.status_code == 200, f"timeline fetch for LLM proof failed: {r.status_code}")
    events = r.json().get("events", [])
    compiled = next(
        (event for event in events if event.get("event_type") == "INTENT_COMPILED"),
        None,
    )
    expect(compiled is not None, "timeline lacks INTENT_COMPILED provenance evidence")
    payload = compiled.get("payload") or {}
    provenance = payload.get("compilation_provenance") or {}
    expect(
        provenance.get("engine") == "llm",
        f"exact basket was not LLM compiled: engine={provenance.get('engine')!r}",
    )
    expected_item_ids = {"monitor-1", "keyboard-1"}
    line_items = contract.get("line_items") or []
    frozen_item_ids = {
        str(line.get("intent_item_id"))
        for line in line_items
        if isinstance(line, dict) and line.get("intent_item_id")
    }
    expect(
        len(line_items) == 2 and frozen_item_ids == expected_item_ids,
        f"frozen contract is not the exact two-line basket: {frozen_item_ids}",
    )
    expect(
        provenance.get("item_count") == 2
        and set(payload.get("item_ids") or []) == expected_item_ids,
        f"persisted LLM provenance does not prove both basket lines: {provenance}",
    )
    detail = (
        f"engine=llm provider={provenance.get('provider') or 'unknown'} "
        f"model={provenance.get('model') or 'unknown'} item_count=2 "
        f"fallback={provenance.get('fallback_reason') or 'none'} "
        f"compiler_version={provenance.get('compiler_version') or 'unknown'} "
        f"retries={provenance.get('validation_retries', 0)}"
    )
    EV.criterion("llm_basket", True, detail)
    log(f"LLM-BASKET: exact two-line monitor+keyboard provenance proven; {detail}")


def load_settings_or_exit() -> Any:
    """Read the SAME settings the API would read (root .env + apps/api/.env)."""
    sys.path.insert(0, str(APPS_API))
    try:
        from project_dante.settings import get_settings
    except Exception as exc:  # noqa: BLE001 - verifier must report broken installs
        print(f"Cannot import project_dante.settings ({exc}); "
              f"cannot confirm razorpay_mode. Run from the repo root.")
        sys.exit(1)
    try:
        return get_settings()
    except Exception as exc:  # noqa: BLE001 - preserve fail-closed settings diagnostics
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


def load_resume_context(
    c: httpx.Client,
    resume_id: str,
) -> tuple[dict[str, Any], str, int, str, str, str]:
    """Read and validate an existing real order for an interrupted run.

    Returns ``(contract, contract_id, amount_paise, order_id, key_id, status)``.
    This path is deliberately read-only: resuming must never reset the store,
    re-authorize a contract, or mint a second Razorpay order.
    """
    r = c.get(f"/api/contracts/{resume_id}")
    expect(r.status_code == 200, f"resume contract fetch failed: {r.status_code} {r.text[:300]}")
    contract = r.json().get("contract") or {}
    cid = str(contract.get("id") or "")
    expect(cid == resume_id, f"resume returned unexpected contract id: {cid!r}")
    status = str(contract.get("status") or "")
    expect(
        status in ("PAYMENT_ORDER_CREATED", "PAYMENT_PENDING", "PAID"),
        f"contract {cid} is not resumable from status {status!r}",
    )
    expect(contract.get("sandbox_mode") is False, "resume contract is not real Test Mode")
    amount_paise = contract.get("amount_paise")
    expect(
        isinstance(amount_paise, int) and not isinstance(amount_paise, bool) and amount_paise > 0,
        f"resume contract has invalid amount: {amount_paise!r}",
    )
    order_id = str(contract.get("razorpay_order_id") or "")
    expect(order_id.startswith("order_"), f"resume order id not Razorpay-shaped: {order_id!r}")

    key_id = ""
    if status in ("PAYMENT_ORDER_CREATED", "PAYMENT_PENDING"):
        r = c.get(f"/api/contracts/{cid}/payment-order")
        expect(
            r.status_code == 200,
            f"resume payment-order fetch failed: {r.status_code} {r.text[:300]}",
        )
        po = r.json()
        expect(po.get("mode") == "live-test-mode", f"resume payment-order mode={po.get('mode')}")
        checkout = po.get("checkout_config") or {}
        expect(
            checkout.get("order_id") == order_id,
            "resume checkout order does not match contract",
        )
        key_id = str(checkout.get("key_id") or "")
        expect(key_id.startswith("rzp_test_"), f"resume checkout key_id not rzp_test_*: {key_id!r}")

    return contract, cid, amount_paise, order_id, key_id, status


def main() -> None:
    global EV

    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--web", default="", help="buyer web app base URL (default: PUBLIC_APP_URL)")
    ap.add_argument("--wait", type=float, default=180.0, help="seconds to wait for the payment")
    ap.add_argument(
        "--resume-contract",
        default="",
        help=(
            "reuse an existing authorized PAYMENT_ORDER_CREATED/PAYMENT_PENDING "
            "contract instead of resetting and creating a new order"
        ),
    )
    ap.add_argument(
        "--no-open",
        action="store_true",
        help="print the checkout URL and wait without opening a local browser",
    )
    args = ap.parse_args()

    settings = load_settings_or_exit()
    preflight_settings(settings)

    c = httpx.Client(
        base_url=args.api.rstrip("/"), timeout=30, headers=operator_headers(settings),
    )
    web_base = (args.web or settings.public_app_url or "http://localhost:3000").rstrip("/")

    if not operator_headers(settings):
        log("WARNING: DEMO_OPERATOR_TOKEN is not set; the live-mode demo fulfillment")
        log("endpoints may answer 403. Export DEMO_OPERATOR_TOKEN and rerun if so.")

    EV = Evidence(STATUS_FILE)
    if not STATUS_FILE.exists():
        STATUS_FILE.write_text("", encoding="utf-8")
    EV.begin()

    fail_msg = ""
    ok = False
    try:
        ok = run_flow(
            c,
            web_base,
            args.wait,
            args.resume_contract,
            open_browser=not args.no_open,
        )
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
        if all_proven:
            try:
                EV.promote_checklist()
                EV.log("checklist: all ten real-integration rows promoted to PROVEN")
            except Exception as exc:  # noqa: BLE001 - evidence must fail closed
                all_proven = False
                fail_msg = f"checklist promotion failed: {exc}"
                log(f"FAIL: {fail_msg}")
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


def run_flow(
    c: httpx.Client,
    web_base: str,
    wait_s: float,
    resume_contract_id: str = "",
    *,
    open_browser: bool = True,
) -> bool:
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

    resume_id = resume_contract_id.strip()
    if resume_id:
        # A timed-out human checkout must be resumable without resetting the
        # store or minting another payable order. Read the contract first, then
        # read back the existing order through the same server-side gate used
        # by a cold-refresh browser page.
        contract, cid, amount_paise, order_id, key_id, status = load_resume_context(c, resume_id)
        log(f"resume: contract={cid} status={status} existing_order={order_id}")
    else:
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
        r = c.post("/api/intents/compile", json={"raw_text": BUNDLE_TEXT})
        expect(r.status_code == 200, f"compile failed: {r.status_code} {r.text[:300]}")
        intent = r.json()["intent"]
        engine = r.json().get("engine")
        iid = intent["id"]
        log(
            f"compile: intent={iid} engine={engine} "
            f"hard_constraints={len(intent.get('hard_constraints', []))} "
            "(LLM never executes money)"
        )
        expect(engine == "llm", f"final LLM basket proof requires engine=llm, got {engine!r}")

        r = c.post(f"/api/intents/{iid}/search")
        expect(r.status_code == 200, f"search failed: {r.status_code} {r.text[:300]}")
        search_body = r.json()
        groups = search_body.get("items") or []
        recommendation = search_body.get("bundle_recommendation") or {}
        expected_item_ids = {"monitor-1", "keyboard-1"}
        expect(
            {str(group.get("item_id")) for group in groups} == expected_item_ids,
            f"search did not return the exact two basket lines: {groups}",
        )
        expect(recommendation.get("available") is True, "no feasible two-line bundle found")
        recommended_ids = recommendation.get("offer_ids") or {}
        expect(set(recommended_ids) == expected_item_ids, "bundle recommendation omitted a line")
        selected_rows: list[dict[str, str]] = []
        selected_skus: list[tuple[str, str | None]] = []
        for group in groups:
            item_id = str(group["item_id"])
            offer_id = str(recommended_ids.get(item_id) or "")
            row = next(
                (
                    candidate
                    for candidate in group.get("results", [])
                    if candidate.get("offer", {}).get("id") == offer_id
                ),
                None,
            )
            expect(row is not None, f"recommended offer missing from group {item_id}")
            expect(
                (row.get("evaluation") or {}).get("feasible") is True,
                f"recommended offer for {item_id} is not hard-feasible",
            )
            selected_rows.append({"item_id": item_id, "offer_id": offer_id})
            selected_skus.append((item_id, row["offer"].get("sku")))
        amount_paise = int(recommendation["total_amount_paise"])
        log(
            f"search: candidates={sum(len(group.get('results', [])) for group in groups)} "
            f"lines={len(groups)} recommended={selected_skus} "
            f"amount_paise={amount_paise}"
        )

        r = c.post(f"/api/intents/{iid}/select-offer", json={"items": selected_rows})
        expect(r.status_code == 200, f"select-offer failed: {r.status_code} {r.text[:300]}")
        body = r.json()
        contract = body["contract"]
        cid = contract["id"]
        n_promises = len(body.get("promises", []))
        expect(n_promises >= 5, f"frozen promises too few: {n_promises}")
        expect(len(contract.get("line_items") or []) == 2, "frozen contract is not a two-line basket")
        expect(contract.get("promise_set_hash"), "promise_set_hash missing")
        log(
            f"freeze: contract={cid} lines={len(contract.get('line_items') or [])} "
            f"promises={n_promises} psh={str(contract.get('promise_set_hash'))[:12]}"
        )

        # ---- 4. authorize ---------------------------------------------------------
        r = c.post(f"/api/contracts/{cid}/authorize", json={})
        expect(r.status_code == 200, f"authorize failed: {r.status_code} {r.text[:300]}")
        log(f"authorize: hash={str(contract.get('contract_hash'))[:12]} scope=two-line-basket")

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

    # This is deliberately checked from the persisted timeline and frozen
    # contract, including resume runs, rather than trusting the compile
    # response or the browser's provenance label.
    record_llm_basket_proof(c, cid, contract)

    # A resumed run reuses the proof-producing order; a fresh run just minted
    # it above. Either way, this criterion is recorded from a real API result.
    order_detail = f"real Razorpay order id {order_id} (amount {amount_paise} paise"
    if key_id:
        order_detail += f", checkout key {redact_checkout_key_id(key_id)})"
    else:
        order_detail += "; checkout key was observed in the original run)"
    EV.criterion("order", True, order_detail)
    log(f"ORDER (REAL): {order_id}")

    # ---- 6. human completes the REAL Standard Checkout payment -----------------
    pay_url = f"{web_base}/contract/{cid}"
    if str(contract.get("status")) != "PAID":
        print()
        print("=" * 78)
        print("ACTION REQUIRED (human): complete the REAL Razorpay Standard Checkout")
        print(f"  Open: {pay_url}")
        print(f"  Order: {order_id}  Amount: {amount_paise} paise")
        print("  (use UPI success@razorpay, or a domestic Indian Test Mode card")
        print("   from Razorpay's current test-card matrix - never a real card)")
        print(f"  Waiting up to {int(wait_s)}s for the signature-verified webhook to grant PAID...")
        print("=" * 78)
        if open_browser:
            try:
                opened = webbrowser.open(pay_url)
            except Exception:  # noqa: BLE001 - headless hosts may lack a browser handler
                opened = False
            browser_state = "opened" if opened else "NOT opened (headless?)"
            log(f"checkout: browser {browser_state} at {pay_url}")
        else:
            log(f"checkout: browser open disabled; manual URL is {pay_url}")
    else:
        log(
            "resume: contract already PAID; skipping checkout prompt and "
            "validating downstream evidence"
        )

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
            if isinstance(v, str) and v.startswith(("evt_", "sha256_")):
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

    # ---- 8-9. synthetic wrong-variant fulfillment + one-line breach -------------
    line_items = [line for line in (contract.get("line_items") or []) if isinstance(line, dict)]
    expect(len(line_items) == 2, "final proof requires exactly two frozen line items")
    affected_line = next(
        (line for line in line_items if line.get("intent_item_id") == "monitor-1"),
        line_items[0],
    )
    affected_line_id = str(affected_line.get("id") or "")
    expect(affected_line_id, "affected monitor line has no frozen line_item_id")
    unaffected_line_ids = {
        str(line.get("id")) for line in line_items if str(line.get("id")) != affected_line_id
    }
    expect(len(unaffected_line_ids) == 1, "final proof needs one unaffected basket line")

    r = c.post(f"/api/demo/contracts/{cid}/ship", json={})
    expect(r.status_code == 200, f"ship failed: {r.status_code} {r.text[:200]} "
                                 "(is X-Demo-Operator-Token accepted by the server gate?)")
    r = c.post(
        f"/api/demo/contracts/{cid}/replacement-unavailable",
        json={"line_item_id": affected_line_id},
    )
    expect(r.status_code == 200, f"scoped replacement-unavailable failed: {r.status_code} {r.text[:200]}")
    r = c.post(
        f"/api/demo/contracts/{cid}/deliver",
        json={"scenario": "wrong_variant", "line_item_id": affected_line_id},
    )
    expect(r.status_code == 200, f"deliver(wrong_variant) failed: {r.status_code} {r.text[:200]}")
    d = r.json()
    expect(d.get("synthetic") is True, "delivery response missing synthetic marker")
    EV.criterion("wrong_variant", True,
                 f"synthetic wrong_variant delivery applied to line {affected_line_id} "
                 "via /demo/deliver with X-Demo-Operator-Token (response synthetic=true)")
    log(f"DELIVERY: wrong_variant line={affected_line_id} (synthetic, operator-token gated)")

    breaches = d.get("breaches", [])
    codes = [b.get("reason_code") or b.get("key") or b.get("promise_key") for b in breaches]
    expect(breaches, f"expected a breach on wrong_variant delivery; got {d}")
    breach_line_ids = {str(b.get("line_item_id")) for b in breaches if b.get("line_item_id")}
    expect(
        breach_line_ids == {affected_line_id},
        f"wrong_variant breached more than the affected line: {breach_line_ids}",
    )
    EV.criterion("breach", True, f"PROMISE_BREACH_DETECTED line={affected_line_id} reasons={codes}")
    log(f"BREACH: line={affected_line_id} reasons={codes}")

    # ---- 10. rights --------------------------------------------------------------
    r = c.get(f"/api/contracts/{cid}/rights")
    expect(r.status_code == 200, f"rights failed: {r.status_code}")
    graph = r.json()["graph"]
    ents = r.json().get("entitlements", [])
    eligible = [e for e in ents if e.get("status") == "eligible"]
    blocked = [e for e in ents if e.get("status") == "blocked"]
    expect(graph.get("nodes"), "empty rights graph")
    eligible_affected = [e for e in eligible if e.get("line_item_id") == affected_line_id]
    expect(
        eligible_affected,
        f"no eligible entitlement for affected line; statuses={[e.get('status') for e in ents]}",
    )
    EV.criterion("rights", True, f"rights graph nodes={len(graph['nodes'])} "
                                 f"edges={len(graph.get('edges', []))} eligible={len(eligible)} "
                                 f"blocked={len(blocked)} affected_line={affected_line_id}")
    log(f"RIGHTS: nodes={len(graph['nodes'])} eligible={len(eligible)} blocked={len(blocked)} line={affected_line_id}")

    # ---- 11. remedy plan + policy --------------------------------------------------
    r = c.get(f"/api/contracts/{cid}/remedies")
    expect(r.status_code == 200, f"remedies failed: {r.status_code}")
    props = r.json()["proposals"]
    chosen = next(
        (
            p
            for p in props
            if p.get("line_item_id") == affected_line_id
            and p.get("rejected_reason") is None
            and p.get("rank") == 1
        ),
        None,
    )
    expect(chosen, f"no chosen remedy proposal for affected line {affected_line_id}")
    expect(chosen["remedy_type"] == "refund_full",
           f"expected refund_full as chosen remedy, got {chosen['remedy_type']}")
    affected_amount = int(affected_line.get("amount_paise") or 0)
    expect(affected_amount > 0, "affected line has no frozen amount")
    expect(
        chosen.get("amount_paise") == affected_amount,
        f"chosen remedy is not capped to affected line: {chosen.get('amount_paise')} vs {affected_amount}",
    )
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
    expect(ma.get("line_item_id") == affected_line_id, "money action lost affected line scope")
    expect(ma.get("amount_paise") == affected_amount, "money action amount escaped line ceiling")
    expect((ex.get("refund") or {}).get("line_item_id") == affected_line_id, "refund lost line scope")
    # This verifier has already proved live-test-mode above.  Accepting the
    # sandbox adapter's ``rf_`` shape here would let a miswired live run be
    # reported as real evidence, so the real-gateway ledger requires the
    # provider's ``rfnd_`` refund resource id.
    expect(
        isinstance(refund_ref, str) and refund_ref.startswith("rfnd_"),
        f"refund id missing/not a real Razorpay refund id (expected rfnd_): {refund_ref!r}",
    )
    EV.criterion("refund", True, f"real Razorpay refund id {refund_ref} line={affected_line_id} "
                                 f"amount_paise={affected_amount} (money_action={ma.get('id')})")
    log(f"REFUND (REAL): {refund_ref} line={affected_line_id} amount_paise={affected_amount}")

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

    final_contract_response = c.get(f"/api/contracts/{cid}")
    expect(final_contract_response.status_code == 200, "final contract fetch failed")
    final_contract = final_contract_response.json().get("contract") or {}
    final_lines = final_contract.get("line_items") or []
    expect(
        {str(line.get("id")) for line in final_lines if isinstance(line, dict)}
        == {str(line.get("id")) for line in line_items},
        "unaffected basket line disappeared during scoped remediation",
    )
    expect(final_contract.get("amount_paise") == amount_paise, "basket total drifted after line refund")

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
    scoped_refunds = [
        e for e in final_events
        if e.get("event_type") == "REFUND_PROCESSED"
        and (e.get("payload") or {}).get("line_item_id") == affected_line_id
    ]
    expect(scoped_refunds, "audit trail lacks the affected line on REFUND_PROCESSED")
    expect(
        not any(
            e.get("event_type") == "REFUND_PROCESSED"
            and (e.get("payload") or {}).get("line_item_id") in unaffected_line_ids
            for e in final_events
        ),
        "audit trail shows a refund against the unaffected line",
    )
    synth = [e for e in final_events if e.get("synthetic")]
    log(f"AUDIT: {len(final_events)} timeline events, {len(synth)} synthetic-labeled, "
        f"scoped_refund_line={affected_line_id}, unaffected_line_preserved=true, terminal={term}")
    return True


if __name__ == "__main__":
    main()

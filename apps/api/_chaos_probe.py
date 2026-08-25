"""Webhook chaos probe — drives the REAL handlers, prints evidence."""
import hashlib
import hmac
import json
import os
import sys
import threading

API = r"X:\RazorPay Buildathon\apps\api"
sys.path.insert(0, API)
os.environ["DANTE_STORE_PATH"] = os.path.join(API, ".chaos-probe-store.json")
os.environ["RAZORPAY_WEBHOOK_SECRET"] = "probe-secret-123"
os.environ["RAZORPAY_KEY_ID"] = ""
os.environ["RAZORPAY_KEY_SECRET"] = ""
os.environ["DEMO_MODE"] = "true"

import asyncio  # noqa: E402

from project_dante.db.store import STORE  # noqa: E402
from project_dante.domain.events import LOG  # noqa: E402
from project_dante.api.routes.webhooks import handle_webhook_bytes  # noqa: E402
from project_dante.integrations.razorpay import service  # noqa: E402


def dumps(obj):
    return json.dumps(obj, separators=(",", ":")).encode()


def deliver(payload: dict, event_id=None, secret=None):
    raw = dumps(payload)
    sig = hmac.new((secret or "probe-secret-123").encode(), raw, hashlib.sha256).hexdigest()
    return asyncio.run(handle_webhook_bytes(raw, sig, event_id))


def captured(order, payment, amount, status="captured", notes=None):
    ent = {"id": payment, "order_id": order, "amount": amount,
           "currency": "INR", "status": status}
    if notes is not None:
        ent["notes"] = notes
    return {"event": "payment.captured", "payload": {"payment": {"entity": ent}}}


def refund_evt(refund_id, payment_id, amount, event="refund.processed"):
    return {"event": event,
            "payload": {"refund": {"entity": {"id": refund_id, "payment_id": payment_id,
                                              "amount": amount, "status": "processed"}}}}


def seed(cid, order, amount=1149900, status="PAYMENT_PENDING", pid=None):
    STORE.put({"_type": "contract", "id": cid, "intent_id": "i_" + cid, "offer_id": "o_" + cid,
               "razorpay_order_id": order, "razorpay_payment_id": pid,
               "amount_paise": amount, "status": status, "sandbox_mode": True})


def cap_events(cid):
    return [e for e in LOG.all() if e.get("aggregate_id") == cid
            and e.get("event_type") == "RAZORPAY_PAYMENT_CAPTURED"]


print("=" * 30, "S1: same order, two DIFFERENT event ids/payments")
STORE.reset(); LOG.reset()
seed("con_S1", "order_S1")
print("d1:", deliver(captured("order_S1", "pay_A", 1149900), "evt_s1_1")[0])
print("mid:", {k: STORE.get("con_S1").get(k) for k in ("status", "razorpay_payment_id")})
print("d2:", deliver(captured("order_S1", "pay_B", 1149900), "evt_s1_2")[0])
c = STORE.get("con_S1")
print("final:", {k: c.get(k) for k in ("status", "razorpay_payment_id")},
      "captures:", len(cap_events("con_S1")))
print("-- refund routing for OLD payment id pay_A (contract now points at pay_B):")
deliver(refund_evt("rf_stale", "pay_A", 50000), "evt_s1_rf")
agg = [e for e in LOG.all() if e.get("event_type") == "REFUND_PROCESSED"]
print("REFUND_PROCESSED aggregates:", [(e["aggregate_type"], e["aggregate_id"]) for e in agg])

print("=" * 30, "S2: two contracts, one shared payment id -> refund routing")
STORE.reset(); LOG.reset()
seed("con_B1", "order_B1", amount=100000)
seed("con_B2", "order_B2", amount=100000)
deliver(captured("order_B1", "pay_SHARED", 100000), "evt_b1")
deliver(captured("order_B2", "pay_SHARED", 100000), "evt_b2")
print("B1:", {k: STORE.get("con_B1").get(k) for k in ("status", "razorpay_payment_id")})
print("B2:", {k: STORE.get("con_B2").get(k) for k in ("status", "razorpay_payment_id")})
deliver(refund_evt("rf_sh", "pay_SHARED", 40000), "evt_brf")
aggs = [(e["aggregate_type"], e["aggregate_id"], e["payload"].get("amount_paise"))
        for e in LOG.all() if e.get("event_type") == "REFUND_PROCESSED"]
print("REFUND_PROCESSED landed on:", aggs)

print("=" * 30, "S3: concurrent captured x2 (distinct event ids), 40 trials")
tally = {}
for trial in range(40):
    STORE.reset(); LOG.reset()
    seed("con_R", "order_R")
    pids = ("pay_R1", "pay_R2") if trial % 2 == 0 else ("pay_R1", "pay_R1")
    raws = [dumps(captured("order_R", p, 1149900)) for p in pids]
    barrier = threading.Barrier(2)
    res = {}

    def run(tag, raw):
        sig = hmac.new(b"probe-secret-123", raw, hashlib.sha256).hexdigest()
        barrier.wait()
        res[tag] = asyncio.run(
            handle_webhook_bytes(raw, sig, f"evt_{tag}_{trial}"))

    ths = [threading.Thread(target=run, args=("a", raws[0])),
           threading.Thread(target=run, args=("b", raws[1]))]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    cc = STORE.get("con_R")
    n_cap = len(cap_events("con_R"))
    key = (cc["status"], n_cap)
    tally[key] = tally.get(key, 0) + 1
print("outcome tally (final_status, #CAPTURED_effects):", tally)

print("=" * 30, "S4: live keys + default webhook secret -> forged capture")
STORE.reset(); LOG.reset()
seed("con_M", "order_M")
raw = dumps(captured("order_M", "pay_FORGED", 1149900))
sig_def = hmac.new(b"dante-dev-webhook-secret", raw, hashlib.sha256).hexdigest()
st, body = asyncio.run(handle_webhook_bytes(raw, sig_def, "evt_forge_def"))
print("delivered with PUBLIC default secret while 'live' keys configured:",
      st, body, "->", STORE.get("con_M")["status"])

print("=" * 30, "S5: full + partial refund stack, one payment")
STORE.reset(); LOG.reset()
seed("con_F", "order_F", amount=100000, status="PAID", pid="pay_F")
STORE.put({"_type": "razorpay_refund", "id": "rf_full_1", "entity": "refund",
           "amount": 100000, "payment_id": "pay_F", "status": "processed",
           "sandbox": True})
deliver(refund_evt("rf_full_1", "pay_F", 100000), "evt_rf_1")
deliver(refund_evt("rf_full_1", "pay_F", 100000, event="refund.completed"), "evt_rf_2")
deliver(refund_evt("rf_never_created_locally", "pay_F", 30000), "evt_rf_3")
evs = [e for e in LOG.all() if e.get("aggregate_id") == "con_F"
       and e.get("event_type") == "REFUND_PROCESSED"]
tot = sum((e.get("payload") or {}).get("amount_paise") or 0 for e in evs)
print(f"REFUND_PROCESSED events on con_F: {len(evs)}  summed amount_paise: {tot} "
      f"(captured=100000)  contract status={STORE.get('con_F')['status']}")

print("=" * 30, "S6a: captured for DELETED contract")
STORE.reset(); LOG.reset()
seed("con_G", "order_G")
STORE.delete("con_G")
st, body = deliver(captured("order_G", "pay_G", 1149900), "evt_g1")
rec = [e for e in LOG.all() if e.get("event_type") == "STATE_RECONCILED"]
print("status:", st, "| reconcile records:", [(e["aggregate_type"], e["aggregate_id"],
      e["payload"].get("reason")) for e in rec])

print("=" * 30, "S6b: capture walks CONTRACT_FROZEN while PENDING-write is missing")
STORE.reset(); LOG.reset()
seed("con_H", "order_H", status="CONTRACT_FROZEN")
deliver(captured("order_H", "pay_H", 1149900), "evt_h1")
h1 = STORE.get("con_H")
caps = [e["payload"] for e in cap_events("con_H")]
print("after h1:", {k: h1.get(k) for k in ("status", "razorpay_payment_id")})
print("h1 CAPTURED payloads:", caps)
recon = [(e["payload"].get("reason"), e["payload"].get("to_status"))
         for e in LOG.for_aggregate("con_H") if e["event_type"] == "STATE_RECONCILED"]
print("h1 STATE_RECONCILED:", recon)
STORE.update("con_H", status="PAYMENT_PENDING")  # the missing write lands later
deliver(captured("order_H", "pay_H", 1149900), "evt_h2")
h2 = STORE.get("con_H")
print("after h2:", {k: h2.get(k) for k in ("status", "razorpay_payment_id")},
      "captures:", len(cap_events("con_H")))

print("=" * 30, "cleanup")
STORE.reset(); LOG.reset()
try:
    os.remove(os.environ["DANTE_STORE_PATH"])
except OSError:
    pass
print("done")

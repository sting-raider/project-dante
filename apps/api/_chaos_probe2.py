"""Chaos probe round 2: concurrency of identical event id, live-key config traps."""
import hashlib
import hmac
import json
import os
import subprocess
import sys
import threading

API = r"X:\RazorPay Buildathon\apps\api"

# ---------------- probe A: N threads, SAME event id (true concurrent redelivery)
CODE_A = r'''
import asyncio, hashlib, hmac, json, os, sys, threading
sys.path.insert(0, sys.argv[1])
os.environ["DANTE_STORE_PATH"] = os.path.join(sys.argv[1], ".chaos-p2a.json")
os.environ["RAZORPAY_WEBHOOK_SECRET"] = "probe-secret-123"
from project_dante.db.store import STORE
from project_dante.domain.events import LOG
from project_dante.api.routes.webhooks import handle_webhook_bytes

STORE.put({"_type":"contract","id":"con_C","intent_id":"i","offer_id":"o",
           "razorpay_order_id":"order_C","razorpay_payment_id":None,
           "amount_paise":100000,"status":"PAYMENT_PENDING","sandbox_mode":True})
payload = {"event":"payment.captured","payload":{"payment":{"entity":{
    "id":"pay_C","order_id":"order_C","amount":100000,"currency":"INR","status":"captured"}}}}
raw = json.dumps(payload, separators=(",",":")).encode()
sig = hmac.new(b"probe-secret-123", raw, hashlib.sha256).hexdigest()
EID = "evt_same_id"

def fire():
    asyncio.run(handle_webhook_bytes(raw, sig, EID))

tally = {}
for trial in range(30):
    LOG.reset(); STORE.update("con_C", status="PAYMENT_PENDING")
    barrier = threading.Barrier(4)
    def run():
        barrier.wait(); fire()
    ts = [threading.Thread(target=run) for _ in range(4)]
    for t in ts: t.start()
    for t in ts: t.join()
    caps = [e for e in LOG.all() if e.get("aggregate_id")=="con_C"
            and e.get("event_type")=="RAZORPAY_PAYMENT_CAPTURED"]
    tally[len(caps)] = tally.get(len(caps),0)+1
print("A: same-event-id x4 threads, 30 trials -> CAPTURED-effects histogram:", tally)
'''

# ---------------- probe B: live keys configured, webhook secret left at default
CODE_B = r'''
import asyncio, hashlib, hmac, json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["DANTE_STORE_PATH"] = os.path.join(sys.argv[1], ".chaos-p2b.json")
os.environ.pop("RAZORPAY_WEBHOOK_SECRET", None)   # operator forgot to set it -> default
os.environ["RAZORPAY_KEY_ID"] = "rzp_test_LiveConfigured"
os.environ["RAZORPAY_KEY_SECRET"] = "some-live-secret"
from project_dante.settings import get_settings
get_settings.cache_clear()
from project_dante.db.store import STORE
from project_dante.domain.events import LOG
from project_dante.api.routes.webhooks import handle_webhook_bytes
from project_dante.integrations.razorpay import service

print("B: mode() =", service.mode(), "| live_test_mode =", get_settings().razorpay_live_test_mode)

STORE.put({"_type":"contract","id":"con_L","intent_id":"i","offer_id":"o",
           "razorpay_order_id":"order_L","razorpay_payment_id":None,
           "amount_paise":100000,"status":"PAYMENT_PENDING","sandbox_mode":True})
payload = {"event":"payment.captured","payload":{"payment":{"entity":{
    "id":"pay_FORGED","order_id":"order_L","amount":100000,"currency":"INR","status":"captured"}}}}
raw = json.dumps(payload, separators=(",",":")).encode()

# anyone who read the PUBLIC repo knows the fallback secret:
forged_sig = hmac.new(b"dante-dev-webhook-secret", raw, hashlib.sha256).hexdigest()
st, body = asyncio.run(handle_webhook_bytes(raw, forged_sig, "evt_forge"))
c = STORE.get("con_L")
print("B: forged capture w/ repo-default secret -> HTTP", st, body,
      "| contract:", {k:c.get(k) for k in ("status","razorpay_payment_id")})

# and the demo surface in this exact configuration:
import httpx
os.environ["DEMO_MODE"]="true"
get_settings.cache_clear()
from fastapi import FastAPI
from fastapi.testclient import TestClient
from project_dante.api.routes.demo import router as demo_router
app = FastAPI(); app.include_router(demo_router, prefix="/api")
cl = TestClient(app)
r_reset = cl.post("/api/demo/reset")
print("B: POST /api/demo/reset with LIVE keys configured ->", r_reset.status_code, r_reset.text[:120])
print("B: contract survived reset?", STORE.get("con_L") is not None,
      "| webhook_event rows left:", STORE.count("webhook_event"))
r_ship = cl.post("/api/demo/contracts/con_L/ship")
print("B: POST /api/demo/contracts/con_L/ship with LIVE keys ->", r_ship.status_code, r_ship.text[:120])
'''

for name, code in (("A", CODE_A), ("B", CODE_B)):
    print(f"===== probe {name} =====")
    r = subprocess.run([os.path.join(API, ".venv", "Scripts", "python.exe"), "-c", code, API],
                       capture_output=True, text=True, timeout=300)
    print(r.stdout.strip())
    if r.returncode != 0:
        print("STDERR:", r.stderr.strip()[-1500:])

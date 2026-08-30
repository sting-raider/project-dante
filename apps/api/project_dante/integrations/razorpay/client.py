"""Razorpay client adapters — one interface, two implementations.

- ``LiveTestModeClient``: real Razorpay **Test Mode** REST API over httpx
  (Basic auth ``key_id:key_secret``, base ``https://api.razorpay.com/v1``).
- ``SandboxClient``: zero-network adapter that mints Razorpay-shaped records,
  persists them in the shared STORE, and computes REAL HMAC-SHA256 signatures
  so every verification path is genuinely exercised before real keys exist.

Invariants honoured here (master plan #9):
- secrets never appear in logs or exception messages;
- amounts are integer paise;
- every sandbox record carries ``"sandbox": true`` (honest labeling);
- refunds are idempotent in BOTH adapters (STORE-checked idempotency key).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import string
import threading
import time
from typing import Any, Protocol

import httpx

from project_dante.db.store import STORE
from project_dante.settings import get_settings

logger = logging.getLogger("project_dante.razorpay")

RAZORPAY_API_BASE = "https://api.razorpay.com/v1"

# Synthetic credentials for the SandboxClient ONLY. These are NOT real keys;
# they exist so HMAC computations are stable and honest about being synthetic.
SANDBOX_KEY_ID = "rzp_test_1DPmmmmmmmmmmm_SANDBOX"
SANDBOX_KEY_SECRET = "dante-sandbox-key-secret-NOT-A-REAL-CREDENTIAL"

# Razorpay resource ids look like ``order_XXXXXXXXXXXXXX`` — a literal prefix
# followed by 14 alphanumeric characters. The sandbox mints the same shape.
_ID_ALPHABET = string.ascii_letters + string.digits
_ID_SUFFIX_LEN = 14

# Refund receipts cap at 40 chars upstream; keep our idempotency receipts legal.
_RECEIPT_MAX = 40

# Upstream refund idempotency (Razorpay docs, POST /payments/:id/refund): send
# header ``X-Refund-Idempotency`` whose value is 10-40 chars of
# [A-Za-z0-9_-]; retrying the same value + body can never create a second
# refund, while a DIFFERENT body under the same value is rejected (409).
_REFUND_IDEMPOTENCY_HEADER = "X-Refund-Idempotency"
_IDEM_VALUE_MAX = 40


class RazorpayError(RuntimeError):
    """Raised when the live Razorpay API rejects or fails a call.

    Never carries authorization material — messages contain status codes and
    truncated provider descriptions only.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


# ------------------------------------------------------------------ signing


def _effective_key_secret() -> str:
    """Secret used for checkout-signature HMACs in the active mode."""
    settings = get_settings()
    return settings.razorpay_key_secret or SANDBOX_KEY_SECRET


def compute_checkout_signature(order_id: str, payment_id: str, *, secret: str | None = None) -> str:
    """HMAC-SHA256 hex digest of ``"{order_id}|{payment_id}"`` (Razorpay spec)."""
    key = secret.encode("utf-8") if secret is not None else _effective_key_secret().encode("utf-8")
    msg = f"{order_id}|{payment_id}".encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def compute_webhook_signature(raw_body: bytes, secret: str) -> str:
    """HMAC-SHA256 hex digest of the RAW webhook body bytes."""
    return hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()


# ------------------------------------------------------------------ protocol


class RazorpayClient(Protocol):
    """The single interface both adapters honour."""

    def create_order(
        self, amount_paise: int, receipt: str = "", notes: dict | None = None
    ) -> dict: ...

    def fetch_payment(self, payment_id: str) -> dict | None: ...

    def fetch_order_payments(self, order_id: str) -> list[dict]: ...

    def fetch_order_by_receipt(self, receipt: str) -> dict | None: ...

    def create_refund(
        self,
        payment_id: str,
        amount_paise: int | None = None,
        idempotency_key: str = "",
        notes: dict | None = None,
    ) -> dict: ...


# ------------------------------------------------------------ idempotency aid


def _existing_refund_for_key(idempotency_key: str) -> dict | None:
    """Return a previously-created refund carrying this idempotency key.

    Used by BOTH adapters before any new refund effect. Retries therefore
    return the original refund unchanged — one key, one financial effect.
    """
    if not idempotency_key:
        return None
    return STORE.find_one("razorpay_refund", idempotency_key=idempotency_key)


def _stored_refunded_total(payment_id: str) -> int:
    """Sum distinct processed local refund records for one payment.

    A refund webhook can arrive before the delayed response to the originating
    POST. In that ordering ``amount_refunded`` already includes the refund, so
    adding the response amount again would overstate the payment projection and
    incorrectly block later refunds. The ledger total is the safe merge value.
    """
    total = 0
    seen: set[str] = set()
    for refund in STORE.find("razorpay_refund", payment_id=payment_id):
        if refund.get("status") not in (None, "processed", "paid"):
            continue
        refund_id = str(refund.get("id") or "")
        if refund_id and refund_id in seen:
            continue
        if refund_id:
            seen.add(refund_id)
        amount = refund.get("amount_paise", refund.get("amount"))
        if isinstance(amount, int) and not isinstance(amount, bool) and amount > 0:
            total += amount
    return total


def refund_idempotency_header_value(idempotency_key: str) -> str:
    """Derive the upstream ``X-Refund-Idempotency`` header value.

    Razorpay requires 10-40 chars of [A-Za-z0-9_-]; our Dante idempotency keys
    (e.g. ``rem_abc123:v2``) contain ``:``/may exceed 40 chars, so we send a
    deterministic digest instead of raw material: sha256 hex truncated to 40
    chars — stable across process restarts, workers, and retries, and always
    inside the documented charset. Empty key => empty string (header omitted).
    """
    if not idempotency_key:
        return ""
    return hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:_IDEM_VALUE_MAX]


# ------------------------------------------------------- LiveTestModeClient


class LiveTestModeClient:
    """Real Razorpay Test Mode REST client (server-side only).

    Synchronous httpx on purpose: the money-executor surface exposed to other
    agents (``service.create_order`` / ``create_refund``) is synchronous by
    frozen contract, and Razorpay test-mode calls are short-lived.
    """

    def __init__(
        self,
        *,
        key_id: str | None = None,
        key_secret: str | None = None,
        base_url: str = RAZORPAY_API_BASE,
        timeout: float = 15.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        settings = get_settings()
        self._key_id = key_id if key_id is not None else settings.razorpay_key_id
        self._key_secret = key_secret if key_secret is not None else settings.razorpay_key_secret
        if not self._key_id or not self._key_secret:
            raise RazorpayError("live-test-mode requires RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET")
        self._base_url = base_url.rstrip("/")
        # NOTE: credentials live only inside this client object; they are never
        # logged, serialized, or included in exceptions raised below.
        self._http = httpx.Client(
            base_url=self._base_url,
            auth=(self._key_id, self._key_secret),
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------ internals

    def _request(
        self,
        method: str,
        path: str,
        json_body: dict | None = None,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict:
        try:
            resp = self._http.request(
                method, path, json=json_body, headers=headers, params=params
            )
        except httpx.HTTPError as exc:  # network/DNS/timeout — no body, no secrets
            kind = type(exc).__name__
            raise RazorpayError(
                f"razorpay transport failure on {method} {path}: {kind}"
            ) from exc
        if resp.status_code >= 400:
            desc = (resp.text or "")[:300].replace("\n", " ")
            raise RazorpayError(
                f"razorpay {method} {path} -> HTTP {resp.status_code}: {desc}",
                status_code=resp.status_code,
            )
        data = resp.json()
        return data if isinstance(data, dict) else {"data": data}

    # -------------------------------------------------------------- orders

    def create_order(
        self, amount_paise: int, receipt: str = "", notes: dict | None = None
    ) -> dict:
        """Create an upstream order.

        ``receipt`` is the order-side idempotency material: Razorpay
        de-duplicates orders per merchant-unique receipt, so the contract-
        scoped receipt handed down from routes/payments.py keeps accidental
        order-recreation retries safe. No extra header is sent — orders have
        no ``X-Refund-Idempotency`` equivalent upstream.
        """
        if not isinstance(amount_paise, int) or isinstance(amount_paise, bool) or amount_paise <= 0:
            raise ValueError("amount_paise must be a positive integer (paise)")
        body: dict[str, Any] = {"amount": amount_paise, "currency": "INR"}
        if receipt:
            body["receipt"] = receipt
        if notes:
            body["notes"] = {str(k): str(v) for k, v in notes.items()}
        order = self._request("POST", "/orders", body)
        order["mode"] = "live-test-mode"
        return order

    # ------------------------------------------------------------ payments

    def fetch_payment(self, payment_id: str) -> dict | None:
        try:
            return self._request("GET", f"/payments/{payment_id}")
        except RazorpayError as exc:
            logger.warning(
                "razorpay fetch_payment failed id=%s status=%s", payment_id, exc.status_code
            )
            return None

    def fetch_order_payments(self, order_id: str) -> list[dict]:
        try:
            data = self._request("GET", f"/orders/{order_id}/payments")
        except RazorpayError as exc:
            logger.warning(
                "razorpay fetch_order_payments failed order=%s status=%s", order_id, exc.status_code
            )
            return []
        items = data.get("items") if isinstance(data, dict) else None
        return items if isinstance(items, list) else []

    def fetch_order_by_receipt(self, receipt: str) -> dict | None:
        """Recover an order after a create response was lost.

        Razorpay exposes receipt filtering on the order collection endpoint;
        return only an exact receipt match because the recovery caller will
        bind this order to a frozen contract.
        """
        if not receipt:
            return None
        try:
            data = self._request(
                "GET", "/orders", params={"receipt": receipt, "count": 100}
            )
        except RazorpayError as exc:
            logger.warning(
                "razorpay fetch_order_by_receipt failed receipt=%s status=%s",
                receipt,
                exc.status_code,
            )
            return None
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return None
        for item in items:
            if isinstance(item, dict) and str(item.get("receipt") or "") == receipt:
                item["mode"] = "live-test-mode"
                return item
        return None

    # ------------------------------------------------------------- refunds

    def create_refund(
        self,
        payment_id: str,
        amount_paise: int | None = None,
        idempotency_key: str = "",
        notes: dict | None = None,
    ) -> dict:
        existing = _existing_refund_for_key(idempotency_key)
        if existing is not None:
            logger.info("refund replay hit local idempotency key=%s…", idempotency_key[:8])
            return existing
        if amount_paise is not None and (
            not isinstance(amount_paise, int) or isinstance(amount_paise, bool) or amount_paise <= 0
        ):
            raise ValueError("amount_paise must be a positive integer (paise)")
        body: dict[str, Any] = {}
        if amount_paise is not None:
            body["amount"] = amount_paise
        if idempotency_key:
            body["receipt"] = idempotency_key[:_RECEIPT_MAX]
        if notes:
            merged = {str(k): str(v) for k, v in notes.items()}
            if idempotency_key:
                merged.setdefault("idempotency_key", idempotency_key)
            body["notes"] = merged
        # Upstream safety net for the lost-response window: when a timeout or
        # connection error hits AFTER Razorpay processed the refund, the local
        # STORE check cannot see the effect — this header makes the retry safe.
        headers = (
            {_REFUND_IDEMPOTENCY_HEADER: refund_idempotency_header_value(idempotency_key)}
            if idempotency_key
            else None
        )
        refund = self._request(
            "POST", f"/payments/{payment_id}/refund", body, headers=headers
        )
        refund["mode"] = "live-test-mode"
        refund.setdefault("idempotency_key", idempotency_key)
        # A refund webhook may win the race and persist this provider id
        # before the POST response reaches us. Merge the response into that
        # record instead of replacing it, otherwise webhook_event_ids,
        # source, and reconciliation metadata disappear from the audit
        # projection when the originating response finally arrives.
        refund_id = str(refund.get("id") or "")
        existing_refund = STORE.get(refund_id) if refund_id else None
        refund_record = {
            "_type": "razorpay_refund",
            **(existing_refund or {}),
            **refund,
        }
        refund_record["_type"] = "razorpay_refund"
        STORE.put(refund_record)
        payment = STORE.get(payment_id)
        if payment is not None and payment.get("_type") == "razorpay_payment":
            prior = payment.get("amount_refunded")
            prior_amount = int(prior) if isinstance(prior, int) and prior >= 0 else 0
            refund_amount = refund.get("amount")
            if not isinstance(refund_amount, int):
                refund_amount = amount_paise or 0
            refund_ids = list(payment.get("processed_refund_ids") or [])
            if refund.get("id") and refund["id"] not in refund_ids:
                refund_ids.append(refund["id"])
            # Merge the provider projection with the distinct local ledger.
            # ``prior_amount + refund_amount`` is wrong when the refund
            # webhook won the race and already projected this same refund.
            ledger_total = _stored_refunded_total(payment_id)
            STORE.update(
                payment_id,
                amount_refunded=max(prior_amount, ledger_total),
                refund_status="processed",
                processed_refund_ids=refund_ids,
                last_refund_id=refund.get("id") or payment.get("last_refund_id"),
            )
        return refund


# --------------------------------------------------------------- SandboxClient


def _mint_id(prefix: str) -> str:
    suffix = "".join(secrets.choice(_ID_ALPHABET) for _ in range(_ID_SUFFIX_LEN))
    return f"{prefix}{suffix}"


_SANDBOX_LOCK = threading.RLock()


class SandboxClient:
    """Deterministic, offline Razorpay stand-in.

    NO network. Mints clearly synthetic Razorpay-shaped ids
    (``order_/pay_/rf_`` + 14 alphanumerics; real Razorpay refunds use ``rfnd_``),
    persists records under STORE types ``razorpay_order`` / ``razorpay_payment`` /
    ``razorpay_refund`` flagged ``"sandbox": true``, and computes REAL
    HMAC-SHA256 signatures so verification paths are exercised for real.
    """

    mode_label = "sandbox"

    # -------------------------------------------------------------- orders

    def create_order(
        self, amount_paise: int, receipt: str = "", notes: dict | None = None
    ) -> dict:
        if not isinstance(amount_paise, int) or isinstance(amount_paise, bool) or amount_paise <= 0:
            raise ValueError("amount_paise must be a positive integer (paise)")
        normalized_notes = {str(k): str(v) for k, v in (notes or {}).items()}
        with _SANDBOX_LOCK:
            if receipt:
                existing = STORE.find_one("razorpay_order", receipt=receipt)
                if existing is not None:
                    if (
                        existing.get("amount") != amount_paise
                        or existing.get("currency") != "INR"
                        or existing.get("notes") != normalized_notes
                    ):
                        raise RazorpayError(
                            "sandbox receipt already used with different order terms",
                            status_code=400,
                        )
                    return dict(existing)

            now = int(time.time())
            record = {
                "_type": "razorpay_order",
                "id": _mint_id("order_"),
                "amount": amount_paise,
                "amount_due": amount_paise,
                "amount_paid": 0,
                "currency": "INR",
                "receipt": receipt,
                "status": "created",
                "attempts": 0,
                "notes": normalized_notes,
                "created_at": now,
                "sandbox": True,
                "mode": "sandbox",
            }
            STORE.put(record)
            return dict(record)

    # ------------------------------------------------------------ payments

    def fetch_payment(self, payment_id: str) -> dict | None:
        rec = STORE.get(payment_id)
        if rec is None or rec.get("_type") != "razorpay_payment":
            return None
        return dict(rec)

    def fetch_order_payments(self, order_id: str) -> list[dict]:
        return [
            r
            for r in STORE.list("razorpay_payment")
            if r.get("order_id") == order_id
        ]

    def fetch_order_by_receipt(self, receipt: str) -> dict | None:
        if not receipt:
            return None
        order = STORE.find_one("razorpay_order", receipt=receipt)
        return dict(order) if order is not None else None

    def capture_sandbox_payment(self, order_id: str, payment_id: str | None = None) -> dict:
        """Mint a CAPTURED payment for a sandbox order (used by the demo
        simulate-event path ONLY — it stands in for Razorpay's own capture).

        Updates the stored order (status ``paid``, attempts+1) exactly like the
        real gateway does, so downstream fetches stay consistent.
        """
        with _SANDBOX_LOCK:
            order = STORE.get(order_id)
            if order is None or order.get("_type") != "razorpay_order":
                raise RazorpayError(f"sandbox order not found: {order_id}", status_code=404)

            if payment_id:
                existing = STORE.get(payment_id)
                if existing is not None:
                    if (
                        existing.get("_type") == "razorpay_payment"
                        and existing.get("order_id") == order_id
                    ):
                        return dict(existing)
                    raise RazorpayError(
                        "sandbox payment id is already bound to another record",
                        status_code=409,
                    )
            else:
                existing = next(
                    (
                        payment
                        for payment in STORE.list("razorpay_payment")
                        if payment.get("order_id") == order_id
                        and payment.get("status") == "captured"
                    ),
                    None,
                )
                if existing is not None:
                    return dict(existing)

            pid = payment_id or _mint_id("pay_")
            record = {
                "_type": "razorpay_payment",
                "id": pid,
                "entity": "payment",
                "amount": order.get("amount"),
                "currency": order.get("currency", "INR"),
                "status": "captured",
                "order_id": order_id,
                "method": "card",
                "captured": True,
                "amount_refunded": 0,
                "refund_status": None,
                "card": {
                    "last4": "1111",
                    "network": "Visa",
                    "type": "credit",
                    "issuer": "HDFC",
                    "name": "dante-test-card",
                },
                "notes": dict(order.get("notes") or {}),
                "created_at": int(time.time()),
                "sandbox": True,
                "mode": "sandbox",
            }
            STORE.put(record)
            STORE.update(
                order_id,
                status="paid",
                amount_paid=order.get("amount"),
                amount_due=0,
                attempts=int(order.get("attempts") or 0) + 1,
            )
            return dict(record)

    # ------------------------------------------------------------- refunds

    def create_refund(
        self,
        payment_id: str,
        amount_paise: int | None = None,
        idempotency_key: str = "",
        notes: dict | None = None,
    ) -> dict:
        with _SANDBOX_LOCK:
            existing = _existing_refund_for_key(idempotency_key)
            if existing is not None:
                logger.info(
                    "sandbox refund replay hit idempotency key=%s…", idempotency_key[:8]
                )
                return existing
            payment = STORE.get(payment_id)
            if payment is None or payment.get("_type") != "razorpay_payment":
                raise RazorpayError(
                    f"sandbox payment not found: {payment_id}", status_code=404
                )
            payable = int(payment.get("amount") or 0) - int(
                payment.get("amount_refunded") or 0
            )
            amount = amount_paise if amount_paise is not None else payable
            if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
                raise ValueError("amount_paise must be a positive integer (paise)")
            if amount > payable:
                raise RazorpayError(
                    f"refund amount {amount} exceeds refundable balance {payable}",
                    status_code=400,
                )
            record = {
                "_type": "razorpay_refund",
                "id": _mint_id("rf_"),
                "entity": "refund",
                "amount": amount,
                "currency": payment.get("currency", "INR"),
                "payment_id": payment_id,
                "order_id": payment.get("order_id"),
                "status": "processed",
                "speed_requested": "normal",
                "notes": {str(k): str(v) for k, v in (notes or {}).items()},
                "created_at": int(time.time()),
                "idempotency_key": idempotency_key,
                "sandbox": True,
                "mode": "sandbox",
            }
            STORE.put(record)
            refund_ids = list(payment.get("processed_refund_ids") or [])
            refund_ids.append(record["id"])
            STORE.update(
                payment_id,
                amount_refunded=int(payment.get("amount_refunded") or 0) + amount,
                refund_status="processed",
                processed_refund_ids=refund_ids,
                last_refund_id=record["id"],
            )
            return dict(record)


# ------------------------------------------------------------------- factory


def get_client() -> RazorpayClient:
    """Pick the adapter implied by settings: real Test Mode when keys exist."""
    if get_settings().razorpay_live_test_mode:
        return LiveTestModeClient()
    return SandboxClient()

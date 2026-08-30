"""Refund idempotency across the LOST-RESPONSE window (network regression).

Two defense layers, tested here at their seams:

Layer 1 (local): STORE-checked idempotency key inside every adapter — a replay
whose result is already known never re-hits the gateway.

Layer 2 (upstream): ``LiveTestModeClient`` sends ``X-Refund-Idempotency``
(Razorpay docs, POST /payments/:id/refund; 10-40 chars of [A-Za-z0-9_-]) whose
value is ``sha256(dante idempotency key)[:40]`` — deterministic across
processes, workers, and retries.

Neither layer can see an effect the gateway COMMITTED but whose response was
lost: a read timeout after processing, a 5xx after processing, a dropped
connection. The tests below simulate exactly that window with
``httpx.MockTransport`` and pin the invariant: one Dante idempotency key =>
ONE financial effect, with the header value byte-identical across attempts —
that equality is what fires the upstream dedup and keeps the retry safe.
"""

from __future__ import annotations

import hashlib
import json

import httpx
import pytest
from project_dante.db.store import STORE
from project_dante.integrations.razorpay import service
from project_dante.integrations.razorpay.client import (
    _REFUND_IDEMPOTENCY_HEADER,
    LiveTestModeClient,
    RazorpayError,
    SandboxClient,
    refund_idempotency_header_value,
)
from project_dante.settings import get_settings

LIVE_KEY_ID = "rzp_" + "test_0123456789" + "abcD"  # concatenated: never a secret-shaped literal
LIVE_KEY_SECRET = "dante-test-key-secret-NOT-REAL"
TEST_WEBHOOK_SECRET = "dante-test-webhook-secret"


@pytest.fixture()
def live_env(tmp_path, monkeypatch):
    """Real-test-key settings (selects LiveTestModeClient) + isolated STORE."""
    monkeypatch.setenv("RAZORPAY_KEY_ID", LIVE_KEY_ID)
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", LIVE_KEY_SECRET)
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", TEST_WEBHOOK_SECRET)
    get_settings.cache_clear()

    saved_records = dict(STORE._records)
    saved_path = STORE._path
    STORE._path = str(tmp_path / "store-refund-network.json")
    STORE.reset()
    yield
    STORE._records.clear()
    STORE._records.update(saved_records)
    STORE._path = saved_path
    get_settings.cache_clear()


class UpstreamSim:
    """Razorpay Test-Mode stand-in with REAL upstream idempotency semantics.

    Every request is recorded (path, idempotency header, JSON body). A refund
    effect commits when a header value is FIRST seen; retries carrying the SAME
    value replay the original refund id and never mint a second one. Scripted
    faults ("timeout" | "conn" | "500") fire AFTER the effect commits — that is
    the lost-response window these regressions exist to pin.
    """

    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []
        self.effects: dict[str, str] = {}  # header value -> refund id
        self.failures: list[str] = []  # FIFO fault script

    def handler(self, request: httpx.Request) -> httpx.Response:
        try:
            body = json.loads(request.content or b"{}")
        except json.JSONDecodeError:
            body = {}
        entry: dict[str, object] = {
            "path": request.url.path,
            # httpx headers are case-insensitive; "" means the header is absent.
            "header": request.headers.get(_REFUND_IDEMPOTENCY_HEADER, ""),
            "body": body,
        }
        self.requests.append(entry)

        fault = self.failures.pop(0) if self.failures else None
        header_value = str(entry["header"])
        refund_id = self.effects.get(header_value)
        if refund_id is None:
            refund_id = f"rf_SIM{len(self.effects) + 1:03d}XXXXXXXXXX"
            self.effects[header_value] = refund_id  # money moved upstream NOW
        if fault == "timeout":
            raise httpx.ReadTimeout("simulated read timeout AFTER processing")
        if fault == "conn":
            raise httpx.ConnectError("simulated connection drop AFTER processing")
        if fault == "500":
            return httpx.Response(
                500, json={"error": {"description": "internal error after processing"}}
            )
        response_amount = (
            body.get("amount", 1149900) if isinstance(body, dict) else 1149900
        )
        return httpx.Response(
            200,
            json={
                "id": refund_id,
                "entity": "refund",
                "amount": response_amount,
                "currency": "INR",
                "payment_id": "pay_SIMULATED00000",
                "status": "processed",
            },
        )

    def headers_seen(self) -> list[str]:
        return [str(e["header"]) for e in self.requests]


class OrderUpstreamSim:
    """Minimal order collection that can lose the POST response."""

    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []
        self.order: dict[str, object] | None = None
        self.fail_create = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(
            {
                "method": request.method,
                "path": request.url.path,
                "receipt": request.url.params.get("receipt", ""),
            }
        )
        if request.method == "POST" and request.url.path == "/orders":
            body = json.loads(request.content or b"{}")
            self.order = {
                "id": "order_RECOVERED0001",
                "amount": body["amount"],
                "currency": body["currency"],
                "receipt": body["receipt"],
                "notes": body.get("notes", {}),
                "status": "created",
            }
            if self.fail_create:
                self.fail_create = False
                raise httpx.ReadTimeout("simulated lost order response")
            return httpx.Response(200, json=self.order)
        if request.method == "GET" and request.url.path == "/orders":
            receipt = request.url.params.get("receipt", "")
            items = [self.order] if self.order and self.order.get("receipt") == receipt else []
            return httpx.Response(200, json={"entity": "collection", "items": items})
        return httpx.Response(404, json={"error": "not found"})


def _client(sim: UpstreamSim) -> LiveTestModeClient:
    return LiveTestModeClient(
        key_id=LIVE_KEY_ID,
        key_secret=LIVE_KEY_SECRET,
        base_url="https://api.razorpay.test",
        transport=httpx.MockTransport(sim.handler),
    )


# ------------------------------------------------------- header derivation


def test_header_name_matches_documented_razorpay_mechanism():
    """Pin the documented header name; a rename here must be a docs event."""
    assert _REFUND_IDEMPOTENCY_HEADER == "X-Refund-Idempotency"


@pytest.mark.parametrize(
    "raw_key", ["rem_abc123:v2", "ma_NET01:full_refund", "k" * 200, "ключ:idem"]
)
def test_header_value_is_sha256_truncated_to_40(raw_key):
    """Deterministic recipe: sha256 hex of the Dante key, truncated to 40.

    Always inside Razorpay's documented charset ([A-Za-z0-9_-], 10-40 chars)
    no matter what characters or length the local key carries.
    """
    got = refund_idempotency_header_value(raw_key)
    assert got == hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:40]
    assert 10 <= len(got) <= 40
    assert all(ch.isalnum() or ch in "_-" for ch in got)


def test_header_value_stable_across_client_instances():
    """Same key => same header value forever (restarts, workers, retries)."""
    k = "rem_stability:v7"
    assert refund_idempotency_header_value(k) == refund_idempotency_header_value(k)


def test_different_keys_yield_different_header_values():
    assert (
        refund_idempotency_header_value("rem_a:v1")
        != refund_idempotency_header_value("rem_b:v1")
    )


# --------------------------------------------------- lost-response scenarios


def test_scenario_a_timeout_after_processing_retries_to_original_refund(live_env):
    """Read timeout AFTER the gateway processed: retry with the SAME header
    returns the ORIGINAL refund id; exactly one STORE refund record exists."""
    sim = UpstreamSim()
    sim.failures.append("timeout")
    client = _client(sim)
    key = "rem_net_a:v1"

    with pytest.raises(RazorpayError):  # attempt 1: response lost
        client.create_refund("pay_SIMA000000000", amount_paise=1149900, idempotency_key=key)

    r2 = client.create_refund("pay_SIMA000000000", amount_paise=1149900, idempotency_key=key)
    expected_header = refund_idempotency_header_value(key)
    assert r2["id"] == sim.effects[expected_header], "retry must recover the original refund"

    # The header was IDENTICAL on both wire attempts — that equality is what
    # made upstream de-duplicate instead of double-refunding.
    assert len(sim.requests) == 2
    assert sim.headers_seen()[0] == sim.headers_seen()[1] == expected_header
    assert len(sim.effects) == 1  # one upstream financial effect total

    # Layer 1: exactly one local record, and a further replay stays local.
    stored = STORE.find("razorpay_refund", idempotency_key=key)
    assert len(stored) == 1 and stored[0]["id"] == r2["id"]
    r3 = client.create_refund("pay_SIMA000000000", amount_paise=1149900, idempotency_key=key)
    assert r3["id"] == r2["id"]
    assert len(sim.requests) == 2, "third call must be served by the local layer"


def test_scenario_b_500_after_processing_retries_to_original_refund(live_env):
    """HTTP 500 AFTER the gateway processed: same convergence guarantee."""
    sim = UpstreamSim()
    sim.failures.append("500")
    client = _client(sim)
    key = "rem_net_b:v1"

    with pytest.raises(RazorpayError) as excinfo:  # ambiguous server failure
        client.create_refund("pay_SIMB000000000", amount_paise=1149900, idempotency_key=key)
    assert excinfo.value.status_code == 500

    r2 = client.create_refund("pay_SIMB000000000", amount_paise=1149900, idempotency_key=key)
    expected_header = refund_idempotency_header_value(key)
    assert r2["id"] == sim.effects[expected_header]
    assert len(sim.requests) == 2
    assert sim.headers_seen()[0] == sim.headers_seen()[1] == expected_header
    assert len(sim.effects) == 1
    stored = STORE.find("razorpay_refund", idempotency_key=key)
    assert len(stored) == 1 and stored[0]["id"] == r2["id"]


def test_scenario_c_connection_drop_then_success_is_one_effect(live_env):
    """First response lost entirely (connection error): the eventual successful
    attempt carries the same header, so exactly one effect exists anywhere."""
    sim = UpstreamSim()
    sim.failures.append("conn")
    client = _client(sim)
    key = "rem_net_c:v1"

    with pytest.raises(RazorpayError):
        client.create_refund("pay_SIMC000000000", idempotency_key=key)  # full refund

    r2 = client.create_refund("pay_SIMC000000000", idempotency_key=key)
    expected_header = refund_idempotency_header_value(key)
    assert r2["id"] == sim.effects[expected_header]

    assert len(sim.requests) == 2
    assert sim.headers_seen()[0] == sim.headers_seen()[1] == expected_header
    assert len(sim.effects) == 1
    assert len(STORE.find("razorpay_refund", idempotency_key=key)) == 1


def test_no_idempotency_key_sends_no_header(live_env):
    """Without a Dante key there is no safe dedup promise to make upstream:
    header omitted honestly (and no local replay is possible either)."""
    sim = UpstreamSim()
    client = _client(sim)

    refund = client.create_refund("pay_SIMN000000000", amount_paise=1000)

    assert refund["id"] == sim.effects[""]
    assert sim.headers_seen() == [""]  # header absent on the wire
    assert len(sim.requests) == 1


# --------------------------------------------- service surface end-to-end


def test_service_create_refund_survives_lost_response_window(live_env, monkeypatch):
    """Public surface (service.create_refund) over a flaky gateway: one key,
    one effect, one STORE record — the contract other agents depend on."""
    sim = UpstreamSim()
    sim.failures.append("timeout")
    monkeypatch.setattr(service, "get_client", lambda: _client(sim))
    key = "rem_net_svc:v1"

    with pytest.raises(RazorpayError):
        service.create_refund("pay_SIMS000000000", amount_paise=250000, idempotency_key=key)

    out = service.create_refund("pay_SIMS000000000", amount_paise=250000, idempotency_key=key)

    assert out["id"] == sim.effects[refund_idempotency_header_value(key)]
    assert len(STORE.find("razorpay_refund", idempotency_key=key)) == 1
    assert set(sim.headers_seen()) == {refund_idempotency_header_value(key)}
    assert len(sim.effects) == 1


def test_service_closes_live_client_after_call(live_env, monkeypatch):
    """The synchronous facade must release each per-call live HTTP client."""
    sim = UpstreamSim()
    client = _client(sim)
    monkeypatch.setattr(service, "get_client", lambda: client)

    service.create_refund(
        "pay_SIMCLOSE00000",
        amount_paise=1000,
        idempotency_key="rem_net_close:v1",
    )

    assert client._http.is_closed is True


def test_live_refund_response_after_webhook_projection_does_not_double_count(live_env):
    """A refund webhook may update the payment before the POST response lands.

    The delayed response must reconcile to the one already-recorded refund,
    not add its amount a second time to ``payment.amount_refunded``.
    """
    sim = UpstreamSim()
    key = "rem_net_webhook_first:v1"
    refund_id = "rf_WEBHOOKFIRST01"
    sim.effects[refund_idempotency_header_value(key)] = refund_id

    payment_id = "pay_SIMULATED00000"
    STORE.put(
        {
            "_type": "razorpay_payment",
            "id": payment_id,
            "amount": 100000,
            "currency": "INR",
            "status": "captured",
            "amount_refunded": 20000,
            "processed_refund_ids": [refund_id],
        }
    )
    STORE.put(
        {
            "_type": "razorpay_refund",
            "id": refund_id,
            "payment_id": payment_id,
            "amount": 20000,
            "status": "processed",
            "webhook_event_id": "evt_refund_projection",
            "webhook_event_ids": ["evt_refund_projection"],
            "source": "webhook",
            # The provider webhook may not echo Dante's local key.
        }
    )

    client = _client(sim)
    refund = client.create_refund(payment_id, amount_paise=20000, idempotency_key=key)

    assert refund["id"] == refund_id
    assert STORE.get(payment_id)["amount_refunded"] == 20000
    assert len(STORE.find("razorpay_refund", payment_id=payment_id)) == 1
    stored = STORE.get(refund_id)
    assert stored is not None
    assert stored["webhook_event_id"] == "evt_refund_projection"
    assert stored["webhook_event_ids"] == ["evt_refund_projection"]
    assert stored["source"] == "webhook"
    assert stored["idempotency_key"] == key


def test_sandbox_keeps_local_layer_semantics(live_env):
    """SandboxClient mirrors the same semantics offline: STORE-checked key,
    replay returns the original refund, one effect — no network involved."""
    client = SandboxClient()
    order = client.create_order(100000)
    payment = client.capture_sandbox_payment(order["id"])

    r1 = client.create_refund(payment["id"], amount_paise=40000, idempotency_key="rem_sb:v1")
    r2 = client.create_refund(payment["id"], amount_paise=40000, idempotency_key="rem_sb:v1")

    assert r1["id"] == r2["id"]
    records = STORE.find("razorpay_refund", idempotency_key="rem_sb:v1")
    assert len(records) == 1
    assert records[0]["sandbox"] is True  # honest labeling invariant


# -------------------------------------------------- orders (requirement 5)


def test_create_order_carries_receipt_as_order_side_dedup_material(live_env):
    """Orders have no X-Refund-Idempotency equivalent upstream; their
    idempotency material is the merchant-unique ``receipt``, which must reach
    the wire verbatim alongside amount/currency — nothing else changes."""
    sim = UpstreamSim()
    client = _client(sim)
    receipt = "dante:con_abc123"  # contract-scoped, <=40 chars (routes/payments.py)

    order = client.create_order(1149900, receipt=receipt, notes={"contract_id": "con_abc123"})

    assert len(sim.requests) == 1
    sent = sim.requests[0]["path"] == "/orders"
    assert sent
    body = sim.requests[0]["body"]
    assert body["receipt"] == receipt  # dedup material delivered upstream
    assert body["amount"] == 1149900  # integer paise invariant
    assert body["currency"] == "INR"
    assert body["notes"] == {"contract_id": "con_abc123"}
    assert sim.requests[0]["header"] == ""  # no refund-style header on orders
    assert order["mode"] == "live-test-mode"


def test_order_receipt_recovery_after_lost_create_response(live_env):
    """A processed POST whose response is lost is recoverable by receipt."""
    sim = OrderUpstreamSim()
    sim.fail_create = True
    client = LiveTestModeClient(
        key_id=LIVE_KEY_ID,
        key_secret=LIVE_KEY_SECRET,
        base_url="https://api.razorpay.test",
        transport=httpx.MockTransport(sim.handler),
    )
    receipt = "dante:con_recovery_01"

    with pytest.raises(RazorpayError):
        client.create_order(
            1149900,
            receipt=receipt,
            notes={"contract_id": "con_recovery_01"},
        )

    recovered = client.fetch_order_by_receipt(receipt)
    assert recovered is not None
    assert recovered["id"] == "order_RECOVERED0001"
    assert recovered["receipt"] == receipt
    assert recovered["mode"] == "live-test-mode"
    assert [request["path"] for request in sim.requests] == ["/orders", "/orders"]
    assert sim.requests[1]["method"] == "GET"
    assert sim.requests[1]["receipt"] == receipt

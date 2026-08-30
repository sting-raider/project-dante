"""Regression coverage for real-integration ledger promotion."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2].parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "verify_real_integration.py"
SPEC = importlib.util.spec_from_file_location("dante_real_integration_verify", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
VERIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY)


def _template() -> str:
    rows = [
        "| # | Criterion | Status | Evidence required | Observed |",
        "| --- | --- | --- | --- | --- |",
    ]
    rows.extend(
        f"| {i} | criterion {cid} | `NOT_YET_PROVEN` | required | none |"
        for i, (cid, _) in enumerate(VERIFY.CRITERIA, start=1)
    )
    return "\n".join(rows) + "\n"


def test_real_ledger_promotes_only_after_complete_run(tmp_path):
    path = tmp_path / "REAL_INTEGRATION_STATUS.md"
    path.write_text(_template(), encoding="utf-8")

    incomplete = VERIFY.Evidence(path)
    incomplete.results["order"] = ("PROVEN", "real order proof")
    with pytest.raises(ValueError, match="incomplete"):
        incomplete.promote_checklist()
    assert "`NOT_YET_PROVEN`" in path.read_text(encoding="utf-8")

    complete = VERIFY.Evidence(path)
    for cid, _ in VERIFY.CRITERIA:
        complete.results[cid] = ("PROVEN", f"proof for {cid}")
    complete.promote_checklist()

    promoted = path.read_text(encoding="utf-8")
    assert promoted.count("`PROVEN`") == len(VERIFY.CRITERIA)
    assert "`NOT_YET_PROVEN`" not in promoted
    for cid, _ in VERIFY.CRITERIA:
        assert f"proof for {cid}" in promoted


class _Response:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload


class _ResumeClient:
    def __init__(self) -> None:
        self.get_paths: list[str] = []
        self.post_called = False

    def get(self, path: str) -> _Response:
        self.get_paths.append(path)
        if path.endswith("/payment-order"):
            return _Response(
                200,
                {
                    "mode": "live-test-mode",
                    "checkout_config": {
                        "key_id": "rzp_test_resume_key",
                        "order_id": "order_resume123456",
                    },
                },
            )
        return _Response(
            200,
            {
                "contract": {
                    "id": "con_resume123456",
                    "status": "PAYMENT_ORDER_CREATED",
                    "sandbox_mode": False,
                    "amount_paise": 649900,
                    "razorpay_order_id": "order_resume123456",
                }
            },
        )

    def post(self, *args, **kwargs) -> _Response:  # noqa: ANN002, ANN003
        self.post_called = True
        raise AssertionError("resume context must not issue a POST")


def test_resume_context_reuses_existing_real_order_without_writes():
    client = _ResumeClient()

    contract, cid, amount, order_id, key_id, status = VERIFY.load_resume_context(
        client, "con_resume123456"
    )

    assert contract["id"] == "con_resume123456"
    assert cid == "con_resume123456"
    assert amount == 649900
    assert order_id == "order_resume123456"
    assert key_id == "rzp_test_resume_key"
    assert status == "PAYMENT_ORDER_CREATED"
    assert client.get_paths == [
        "/api/contracts/con_resume123456",
        "/api/contracts/con_resume123456/payment-order",
    ]
    assert client.post_called is False


def test_operator_headers_fall_back_to_dotenv_backed_settings(monkeypatch):
    monkeypatch.delenv("DEMO_OPERATOR_TOKEN", raising=False)

    assert VERIFY.operator_headers(SimpleNamespace(demo_operator_token="from-settings")) == {
        "X-Demo-Operator-Token": "from-settings"
    }

    monkeypatch.setenv("DEMO_OPERATOR_TOKEN", "from-process")
    assert VERIFY.operator_headers(SimpleNamespace(demo_operator_token="from-settings")) == {
        "X-Demo-Operator-Token": "from-process"
    }

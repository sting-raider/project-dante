"""Backend-independent atomic store primitives."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from project_dante.db.store import Store


def test_put_if_absent_allows_one_webhook_claim(tmp_path):
    store = Store(str(tmp_path / "store.json"))
    record = {"_type": "webhook_event", "id": "evt_claim_once", "processing_status": "processing"}

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: store.put_if_absent(record), range(8)))

    assert sum(results) == 1
    assert store.get("evt_claim_once") == record


def test_update_if_is_compare_and_swap(tmp_path):
    store = Store(str(tmp_path / "store.json"))
    store.put({"_type": "contract", "id": "con_cas", "status": "PAYMENT_PENDING"})

    assert store.update_if(
        "con_cas", {"status": "PAYMENT_PENDING"}, status="PAID"
    ) is True
    assert store.update_if(
        "con_cas", {"status": "PAYMENT_PENDING"}, status="FAILED"
    ) is False
    assert store.get("con_cas")["status"] == "PAID"  # type: ignore[index]

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


def test_separate_store_instances_do_not_clobber_newer_snapshot(tmp_path):
    """A stale API process must merge, never replace, another process's write."""
    path = str(tmp_path / "shared-store.json")
    contract_writer = Store(path)
    webhook_writer = Store(path)

    contract = {"_type": "contract", "id": "con_shared", "status": "PAYMENT_PENDING"}
    webhook = {
        "_type": "webhook_event",
        "id": "evt_shared",
        "event_type": "payment.captured",
    }

    contract_writer.put(contract)
    webhook_writer.put(webhook)

    restarted = Store(path)
    assert restarted.get("con_shared") == contract
    assert restarted.get("evt_shared") == webhook


def test_store_reader_refreshes_after_external_writer(tmp_path):
    path = str(tmp_path / "shared-store.json")
    reader = Store(path)
    writer = Store(path)

    writer.put({"_type": "contract", "id": "con_visible", "status": "PAID"})

    assert reader.get("con_visible") == {
        "_type": "contract",
        "id": "con_visible",
        "status": "PAID",
    }

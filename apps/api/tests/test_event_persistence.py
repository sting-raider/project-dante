from __future__ import annotations

from pathlib import Path

from project_dante.db.store import Store
from project_dante.domain.events import DomainEvent, EventLog


def _event(*, idempotency_key: str | None = None) -> dict:
    return DomainEvent.create(
        aggregate_type="contract",
        aggregate_id="con_restart",
        event_type="CONTRACT_CREATED",
        payload={"display_code": "COV-0001"},
        idempotency_key=idempotency_key,
    )


def test_event_log_rehydrates_persisted_events_and_idempotency(tmp_path: Path):
    path = tmp_path / "events.json"
    first_store = Store(str(path))
    first_log = EventLog(first_store)
    event = _event(idempotency_key="contract-created:v1")

    assert first_log.append(event) == event
    assert first_store.find("domain_event") == [{**event, "_type": "domain_event"}]

    restarted_log = EventLog(Store(path))
    assert restarted_log.all() == [event]
    assert restarted_log.append(_event(idempotency_key="contract-created:v1")) is None


def test_event_log_reset_removes_persisted_events(tmp_path: Path):
    store = Store(str(tmp_path / "events.json"))
    log = EventLog(store)
    log.append(_event())

    assert log.reset() == 1
    assert store.find("domain_event") == []
    assert EventLog(Store(str(store._path))).all() == []

"""Append-only domain event stream — canonical audit history (plan §21).

The process-local log is backed by the configured Dante store.  Keeping the
small in-memory index preserves the existing synchronous API while typed
``domain_event`` records make the audit timeline survive API restarts for
both the JSON and Postgres store backends.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

EVENT_TYPES = [
    "INTENT_RECEIVED",
    "INTENT_COMPILED",
    "CATALOG_SEARCHED",
    "OFFER_EVALUATED",
    "OFFER_SELECTED",
    "EVIDENCE_SNAPSHOT_CREATED",
    "PROMISE_SET_FROZEN",
    "CONTRACT_CREATED",
    "BUYER_AUTHORIZED",
    "RAZORPAY_ORDER_CREATED",
    "CHECKOUT_COMPLETED_CLIENT",
    "PAYMENT_VERIFIED_SERVER",
    "RAZORPAY_PAYMENT_CAPTURED",
    "FULFILLMENT_SHIPPED",
    "FULFILLMENT_DELIVERED",
    "OBSERVED_FACT_RECORDED",
    "PROMISE_BREACH_DETECTED",
    "RIGHTS_REEVALUATED",
    "REMEDY_PROPOSED",
    "POLICY_DECIDED",
    "POLICY_ALLOWED",
    "POLICY_DENIED",
    "REFUND_REQUESTED",
    "REFUND_PROCESSED",
    "REFUND_FAILED",
    "CONTRACT_SATISFIED",
    "CONTRACT_REMEDIATED",
    "WEBHOOK_RECEIVED",
    "WEBHOOK_DUPLICATE_IGNORED",
    "STATE_RECONCILED",
]

CATEGORY_BY_EVENT: dict[str, str] = {}
for _names, _cat in [
    (
        [
            "INTENT_RECEIVED",
            "INTENT_COMPILED",
            "OFFER_EVALUATED",
            "OFFER_SELECTED",
            "REMEDY_PROPOSED",
        ],
        "Agent",
    ),
    (
        [
            "RAZORPAY_ORDER_CREATED",
            "CHECKOUT_COMPLETED_CLIENT",
            "PAYMENT_VERIFIED_SERVER",
            "RAZORPAY_PAYMENT_CAPTURED",
            "REFUND_REQUESTED",
            "REFUND_PROCESSED",
            "REFUND_FAILED",
            "POLICY_ALLOWED",
            "POLICY_DENIED",
            "POLICY_DECIDED",
        ],
        "Money",
    ),
    (["CATALOG_SEARCHED"], "Merchant"),
    (["FULFILLMENT_SHIPPED", "FULFILLMENT_DELIVERED"], "Fulfillment"),
    (["PROMISE_BREACH_DETECTED", "RIGHTS_REEVALUATED"], "Policy"),
    (
        [
            "EVIDENCE_SNAPSHOT_CREATED",
            "OBSERVED_FACT_RECORDED",
            "WEBHOOK_RECEIVED",
            "WEBHOOK_DUPLICATE_IGNORED",
            "STATE_RECONCILED",
        ],
        "Evidence",
    ),
]:
    for _n in _names:
        CATEGORY_BY_EVENT[_n] = _cat


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class DomainEvent(dict):
    """Lightweight event record (dict-based for JSON persistence)."""

    @classmethod
    def create(
        cls,
        *,
        aggregate_type: str,
        aggregate_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        idempotency_key: str | None = None,
        trace_id: str | None = None,
        synthetic: bool = False,
        scenario_id: str | None = None,
    ) -> DomainEvent:
        if event_type not in EVENT_TYPES:
            raise ValueError(f"Unknown event type: {event_type}")
        return cls(
            id=uuid4().hex,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            category=CATEGORY_BY_EVENT.get(event_type, "System"),
            event_version=1,
            payload=payload or {},
            correlation_id=correlation_id,
            causation_id=causation_id,
            idempotency_key=idempotency_key,
            trace_id=trace_id,
            synthetic=synthetic,
            scenario_id=scenario_id,
            created_at=now_iso(),
        )


class EventLog:
    """Thread-safe append-only event log with durable idempotency recovery."""

    def __init__(self, store: Any | None = None) -> None:
        self._events: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._idem_seen: set[tuple[str, str]] = set()
        if store is None:
            # Lazy import keeps the store/event modules free of an import
            # cycle while still letting isolated tests inject a temp store.
            from project_dante.db.store import STORE

            store = STORE
        self._store = store
        self._load_persisted()

    @staticmethod
    def _event_from_record(record: dict[str, Any]) -> dict[str, Any]:
        """Remove the store discriminator before exposing an event."""
        return {key: value for key, value in record.items() if key != "_type"}

    def _load_persisted(self) -> None:
        try:
            records = self._store.list("domain_event")
        except Exception:  # noqa: BLE001 - startup must remain fail-safe
            records = []
        events = [
            self._event_from_record(record)
            for record in records
            if isinstance(record, dict) and isinstance(record.get("id"), str)
        ]
        events.sort(
            key=lambda event: (
                str(event.get("created_at") or ""),
                str(event.get("id") or ""),
            )
        )
        with self._lock:
            self._events = events
            self._idem_seen = {
                (event["aggregate_id"], event["idempotency_key"])
                for event in events
                if isinstance(event.get("aggregate_id"), str)
                and isinstance(event.get("idempotency_key"), str)
                and bool(event.get("idempotency_key"))
            }

    def append(self, event: dict[str, Any]) -> dict[str, Any] | None:
        aggregate_id = event.get("aggregate_id")
        idempotency_key = event.get("idempotency_key")
        with self._lock:
            dedupe_key: tuple[str, str] | None = None
            # DomainEvent.create() supplies both values as strings. Keep the
            # generic dict boundary fail-safe: malformed events must not put
            # None or arbitrary values into the typed deduplication index.
            if (
                isinstance(aggregate_id, str)
                and isinstance(idempotency_key, str)
                and idempotency_key
            ):
                key = (aggregate_id, idempotency_key)
                if key in self._idem_seen:
                    return None  # duplicate suppressed
                dedupe_key = key
            # Store events separately from business records so callers keep
            # receiving the original event shape (without ``_type``).  The
            # write occurs while the same lock protects the idempotency set,
            # so a restart cannot resurrect an event that was only partially
            # claimed in memory.  Claim the idempotency key only after the
            # durable write succeeds, allowing a caller to retry a failed
            # persistence attempt safely.
            if isinstance(event.get("id"), str):
                persisted = dict(event)
                persisted["_type"] = "domain_event"
                self._store.put(persisted)
            if dedupe_key is not None:
                self._idem_seen.add(dedupe_key)
            self._events.append(dict(event))
            return event

    def all(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._events)

    def for_aggregate(self, aggregate_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [e for e in self._events if e.get("aggregate_id") == aggregate_id]

    def reset(self) -> int:
        with self._lock:
            n = len(self._events)
            persisted_ids = [
                event.get("id")
                for event in self._events
                if isinstance(event.get("id"), str)
            ]
            self._events.clear()
            self._idem_seen.clear()
            for event_id in persisted_ids:
                self._store.delete(event_id)
            return n


LOG = EventLog()


def append_event(**kwargs: Any) -> dict[str, Any] | None:
    """Create + append in one call."""
    return LOG.append(DomainEvent.create(**kwargs))

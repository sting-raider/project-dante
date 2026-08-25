"""Append-only domain event stream — canonical audit history (plan §21).

Events are persisted by the events service (db layer); this module defines
the vocabulary, ordering helpers, and in-memory append-only log used when
running without Postgres.
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
    """Thread-safe append-only event log with idempotency-key dedup."""

    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._idem_seen: set[tuple[str, str]] = set()

    def append(self, event: dict[str, Any]) -> dict[str, Any] | None:
        key = (event.get("aggregate_id"), event.get("idempotency_key"))
        with self._lock:
            if event.get("idempotency_key"):
                if key in self._idem_seen:
                    return None  # duplicate suppressed
                self._idem_seen.add(key)
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
            self._events.clear()
            self._idem_seen.clear()
            return n


LOG = EventLog()


def append_event(**kwargs: Any) -> dict[str, Any] | None:
    """Create + append in one call."""
    return LOG.append(DomainEvent.create(**kwargs))

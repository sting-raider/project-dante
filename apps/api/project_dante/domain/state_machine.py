"""Contract lifecycle state machine.

Transitions are validated here and nowhere else. Any attempt to move a
contract through an unlisted transition raises InvalidTransition.
"""

from __future__ import annotations

from project_dante.domain.types import ContractStatus

D = ContractStatus

# Legal transitions: from -> allowed targets
TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"INTENT_READY", "CANCELLED", "FAILED"},
    "INTENT_READY": {"OFFER_SELECTED", "CANCELLED", "FAILED"},
    "OFFER_SELECTED": {"CONTRACT_FROZEN", "CANCELLED", "FAILED"},
    "CONTRACT_FROZEN": {"AWAITING_BUYER_AUTH", "CANCELLED", "FAILED"},
    "AWAITING_BUYER_AUTH": {
        "PAYMENT_ORDER_CREATED",
        "CONTRACT_FROZEN",  # offer drift -> re-freeze
        "CANCELLED",
        "FAILED",
    },
    "PAYMENT_ORDER_CREATED": {"PAYMENT_PENDING", "FAILED", "CANCELLED"},
    "PAYMENT_PENDING": {"PAID", "FAILED", "CANCELLED", "PAYMENT_ORDER_CREATED"},
    "PAID": {"FULFILLING", "VERIFYING", "FAILED"},
    "FULFILLING": {"DELIVERED", "BREACH_DETECTED", "FAILED"},
    "DELIVERED": {"VERIFYING", "BREACH_DETECTED", "SATISFIED", "FAILED"},
    "VERIFYING": {"SATISFIED", "BREACH_DETECTED", "FAILED"},
    "SATISFIED": set(),  # terminal
    "BREACH_DETECTED": {
        "REMEDY_PLANNING",
        "REMEDIATED",  # informational breach, no action needed
        "FAILED",
    },
    "REMEDY_PLANNING": {
        "AWAITING_REMEDY_APPROVAL",
        "REMEDIATED",
        "BREACH_DETECTED",  # no valid remedy found -> stay breached
        "FAILED",
    },
    "AWAITING_REMEDY_APPROVAL": {"REMEDY_EXECUTING", "REMEDY_PLANNING", "FAILED"},
    "REMEDY_EXECUTING": {"REMEDIATED", "BREACH_DETECTED", "FAILED"},
    "REMEDIATED": set(),  # terminal
    "CANCELLED": set(),  # terminal
    "FAILED": set(),  # terminal
}


class InvalidTransition(Exception):
    def __init__(self, current: str, target: str) -> None:
        self.current = current
        self.target = target
        super().__init__(f"Illegal contract transition {current} -> {target}")


def can_transition(current: str, target: str) -> bool:
    return target in TRANSITIONS.get(current, set())


def validate_transition(current: str, target: str) -> None:
    if not can_transition(current, target):
        raise InvalidTransition(current, target)

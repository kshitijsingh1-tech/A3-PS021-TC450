"""Explicit lifecycle state machine for dialog tasks (PS-021).

Enforces valid state transitions, terminal state immutability,
and retry rules.
"""

from __future__ import annotations

from typing import Set, Dict
from aiori.schema import DialogStatus


class StateTransitionError(Exception):
    """Raised when an illegal lifecycle transition is attempted."""
    pass


class TerminalStateError(StateTransitionError):
    """Raised when an update is attempted on an already COMPLETED task."""
    pass


class OptimisticLockError(Exception):
    """Raised when a concurrent update modifies a record before commit."""
    pass


class IdempotencyConflictError(Exception):
    """Raised when the same idempotency key is reused with a different payload."""
    pass


# Mapping of valid transitions: current_status -> set of allowed next_statuses
VALID_TRANSITIONS: Dict[DialogStatus, Set[DialogStatus]] = {
    DialogStatus.RECEIVED: {
        DialogStatus.ACCEPTED,
        DialogStatus.RECOVERING,
        DialogStatus.FAILED,
    },
    DialogStatus.ACCEPTED: {
        DialogStatus.RUNNING,
        DialogStatus.RECOVERING,
        DialogStatus.FAILED,
    },
    DialogStatus.RUNNING: {
        DialogStatus.COMPLETED,
        DialogStatus.RECOVERING,
        DialogStatus.FAILED,
    },
    DialogStatus.RECOVERING: {
        DialogStatus.RUNNING,
        DialogStatus.COMPLETED,
        DialogStatus.FAILED,
    },
    DialogStatus.FAILED: {
        DialogStatus.ACCEPTED,  # Only permitted under explicit retry policy
    },
    DialogStatus.COMPLETED: set(),  # Terminal state: zero outgoing transitions allowed
}


class LifecycleStateMachine:
    """Validator and manager of lifecycle transitions."""

    @classmethod
    def can_transition(cls, from_status: DialogStatus, to_status: DialogStatus) -> bool:
        """Check whether a transition from from_status to to_status is permitted."""
        if from_status == DialogStatus.COMPLETED:
            return False
        return to_status in VALID_TRANSITIONS.get(from_status, set())

    @classmethod
    def validate_transition(cls, from_status: DialogStatus, to_status: DialogStatus) -> None:
        """Validate transition, raising an appropriate exception if invalid."""
        if from_status == DialogStatus.COMPLETED:
            raise TerminalStateError(
                f"Cannot transition from terminal state COMPLETED to {to_status.value}."
            )
        allowed = VALID_TRANSITIONS.get(from_status, set())
        if to_status not in allowed:
            raise StateTransitionError(
                f"Illegal state transition from {from_status.value} to {to_status.value}. "
                f"Allowed destinations: {[s.value for s in allowed]}"
            )

"""AIORI: Experimental Dialog Correlation and Recovery Across Agent Adapters (PS-021)."""

from aiori.schema import (
    DialogMessage,
    DialogStatus,
    DialogStatusResponse,
    SCHEMA_VERSION,
    compute_canonical_payload_hash,
)
from aiori.state_machine import (
    LifecycleStateMachine,
    StateTransitionError,
    TerminalStateError,
    OptimisticLockError,
    IdempotencyConflictError,
)
from aiori.db import DatabaseManager
from aiori.idempotency import IdempotencyEngine
from aiori.recovery import StartupRecoveryManager
from aiori.client import AdapterAClient

__all__ = [
    "DialogMessage",
    "DialogStatus",
    "DialogStatusResponse",
    "SCHEMA_VERSION",
    "compute_canonical_payload_hash",
    "LifecycleStateMachine",
    "StateTransitionError",
    "TerminalStateError",
    "OptimisticLockError",
    "IdempotencyConflictError",
    "DatabaseManager",
    "IdempotencyEngine",
    "StartupRecoveryManager",
    "AdapterAClient",
]

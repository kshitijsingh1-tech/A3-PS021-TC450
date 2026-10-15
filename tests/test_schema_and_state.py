"""Tests for Schema validation, payload canonical hashing, and state machine transitions."""

import pytest
from aiori.schema import (
    DialogMessage,
    DialogStatus,
    compute_canonical_payload_hash,
)
from aiori.state_machine import (
    LifecycleStateMachine,
    StateTransitionError,
    TerminalStateError,
)


def test_payload_canonical_hashing_consistency():
    """Verify that payload hashing is deterministic regardless of key ordering."""
    p1 = {"b": 2, "a": 1, "nested": {"z": 10, "y": 20}}
    p2 = {"nested": {"y": 20, "z": 10}, "a": 1, "b": 2}
    hash1 = compute_canonical_payload_hash(p1)
    hash2 = compute_canonical_payload_hash(p2)
    assert hash1 == hash2
    assert len(hash1) == 64

    # Different payload must yield different hash
    p3 = {"a": 1, "b": 3}
    assert compute_canonical_payload_hash(p3) != hash1


def test_dialog_message_validation():
    """Verify Pydantic envelope validation and defaults."""
    msg = DialogMessage(
        dialog_id="diag_100",
        request_id="req_100",
        idempotency_key="idemp_100",
        source_adapter="adapter_a",
        target_adapter="adapter_b",
        task_type="payment_transfer",
        payload={"amount": 50.0},
    )
    assert msg.schema_version == "1.0.0-exp"
    assert msg.status == DialogStatus.RECEIVED
    assert msg.attempt == 1
    assert msg.version == 1
    assert len(msg.canonical_payload_hash()) == 64


def test_schema_version_rejection():
    """Verify unsupported schema versions are rejected."""
    with pytest.raises(ValueError, match="Unsupported schema version"):
        DialogMessage(
            schema_version="2.0.0-unsupported",
            dialog_id="diag_100",
            request_id="req_100",
            idempotency_key="idemp_100",
            source_adapter="adapter_a",
            target_adapter="adapter_b",
            task_type="payment_transfer",
        )


def test_state_machine_valid_transitions():
    """Verify standard happy-path transitions."""
    LifecycleStateMachine.validate_transition(DialogStatus.RECEIVED, DialogStatus.ACCEPTED)
    LifecycleStateMachine.validate_transition(DialogStatus.ACCEPTED, DialogStatus.RUNNING)
    LifecycleStateMachine.validate_transition(DialogStatus.RUNNING, DialogStatus.COMPLETED)


def test_state_machine_recovery_transitions():
    """Verify valid recovery transitions."""
    LifecycleStateMachine.validate_transition(DialogStatus.RUNNING, DialogStatus.RECOVERING)
    LifecycleStateMachine.validate_transition(DialogStatus.RECOVERING, DialogStatus.RUNNING)
    LifecycleStateMachine.validate_transition(DialogStatus.RECOVERING, DialogStatus.COMPLETED)
    LifecycleStateMachine.validate_transition(DialogStatus.RECOVERING, DialogStatus.FAILED)


def test_state_machine_terminal_state_rejection():
    """Verify that COMPLETED is strictly terminal."""
    with pytest.raises(TerminalStateError, match="terminal state COMPLETED"):
        LifecycleStateMachine.validate_transition(DialogStatus.COMPLETED, DialogStatus.RUNNING)

    with pytest.raises(TerminalStateError, match="terminal state COMPLETED"):
        LifecycleStateMachine.validate_transition(DialogStatus.COMPLETED, DialogStatus.RECOVERING)

    assert LifecycleStateMachine.can_transition(DialogStatus.COMPLETED, DialogStatus.RUNNING) is False


def test_state_machine_illegal_transitions():
    """Verify arbitrary illegal jumps raise StateTransitionError."""
    with pytest.raises(StateTransitionError):
        LifecycleStateMachine.validate_transition(DialogStatus.RECEIVED, DialogStatus.COMPLETED)

    with pytest.raises(StateTransitionError):
        LifecycleStateMachine.validate_transition(DialogStatus.ACCEPTED, DialogStatus.COMPLETED)

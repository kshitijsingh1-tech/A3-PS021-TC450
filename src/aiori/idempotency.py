"""Durable Idempotency Engine for PS-021.

Coordinates atomic registration, conflict detection, deterministic deduplication,
and transactional lifecycle execution with fault injection hooks.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any, Dict, Optional, Tuple

from aiori.db import DatabaseManager
from aiori.schema import DialogMessage, DialogStatus
from aiori.side_effects import SideEffectExecutor
from aiori.state_machine import (
    IdempotencyConflictError,
    OptimisticLockError,
    TerminalStateError,
)

logger = logging.getLogger("aiori.idempotency")


class FaultInjectionTrigger(Exception):
    """Simulated fault injection exception (used when process crash is not invoked via os._exit)."""
    pass


class IdempotencyEngine:
    """Core idempotency orchestrator."""

    def __init__(self, db: DatabaseManager):
        self.db = db

    def process_message(
        self,
        msg: DialogMessage,
        fault_action: Optional[str] = None,
    ) -> Tuple[DialogMessage, int]:
        """Process an incoming dialog message through the idempotency state machine.

        Args:
            msg: The incoming DialogMessage envelope.
            fault_action: Optional fault injection directive (e.g.,
                'crash_before_execution', 'crash_after_commit', 'simulate_failure').

        Returns:
            (DialogMessage, http_status_code)
        """
        # Step 1: Atomic registration or lookup relying on uniqueness constraint
        is_new, op = self.db.register_or_get_operation(msg)

        # Case A: Pre-existing request
        if not is_new:
            # 1. Duplicate completed request: return stored result immediately
            if op.status == DialogStatus.COMPLETED:
                logger.info(
                    "Duplicate request for completed task %s. Returning stored result.",
                    op.idempotency_key,
                )
                return op, 200

            # 2. Duplicate in-progress request (RECEIVED, ACCEPTED, RUNNING, RECOVERING)
            if op.status in {
                DialogStatus.RECEIVED,
                DialogStatus.ACCEPTED,
                DialogStatus.RUNNING,
                DialogStatus.RECOVERING,
            }:
                logger.info(
                    "Duplicate request for in-progress task %s (status=%s). Returning in-progress.",
                    op.idempotency_key,
                    op.status.value,
                )
                # Return current in-progress state with 202 Accepted
                return op, 202

            # 3. Duplicate failed request: check if explicit retry is permitted
            if op.status == DialogStatus.FAILED:
                logger.info(
                    "Task %s was previously FAILED. Evaluating retry attempt %d -> %d.",
                    op.idempotency_key,
                    op.attempt,
                    msg.attempt,
                )
                # If the incoming message has a higher attempt number, transition FAILED -> ACCEPTED
                if msg.attempt > op.attempt:
                    with self.db.transaction() as conn:
                        op = self.db.update_operation_status(
                            conn=conn,
                            idempotency_key=op.idempotency_key,
                            expected_version=op.version,
                            target_status=DialogStatus.ACCEPTED,
                            request_id=msg.request_id,
                            attempt=msg.attempt,
                            audit_details=f"Retrying failed task at attempt {msg.attempt}",
                        )
                else:
                    return op, 200

        # Case B: Execution path (for new requests or re-accepted retries)
        # Advance from RECEIVED to ACCEPTED if newly inserted
        if op.status == DialogStatus.RECEIVED:
            with self.db.transaction() as conn:
                op = self.db.update_operation_status(
                    conn=conn,
                    idempotency_key=op.idempotency_key,
                    expected_version=op.version,
                    target_status=DialogStatus.ACCEPTED,
                    request_id=msg.request_id,
                    attempt=msg.attempt,
                    audit_details="Transitioned to ACCEPTED",
                )

        # Fault Injection: crash before execution
        if fault_action == "crash_before_execution":
            logger.warning("FAULT INJECTION: Crashing process before execution!")
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(42)

        # Advance from ACCEPTED to RUNNING
        if op.status == DialogStatus.ACCEPTED:
            with self.db.transaction() as conn:
                op = self.db.update_operation_status(
                    conn=conn,
                    idempotency_key=op.idempotency_key,
                    expected_version=op.version,
                    target_status=DialogStatus.RUNNING,
                    audit_details="Starting task execution",
                )

        # Fault Injection: crash during execution (before committing side effect)
        if fault_action == "crash_during_execution":
            logger.warning("FAULT INJECTION: Crashing process during execution (uncommitted)!")
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(43)

        # Execute business side effect and commit atomically with COMPLETED state
        with self.db.transaction() as conn:
            # Execute business logic (inserts into side_effects table)
            result = SideEffectExecutor.execute(conn=conn, db=self.db, msg=op)

            # Atomically update operation to COMPLETED
            op = self.db.update_operation_status(
                conn=conn,
                idempotency_key=op.idempotency_key,
                expected_version=op.version,
                target_status=DialogStatus.COMPLETED,
                result=result,
                audit_details="Completed execution and committed side effect",
            )

        # Fault Injection: crash after commit (side effect is safely in DB, but client gets drop/crash)
        if fault_action == "crash_after_commit":
            logger.warning("FAULT INJECTION: Crashing process after successful commit!")
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(44)

        if fault_action == "drop_response":
            logger.warning("FAULT INJECTION: Dropping HTTP response without sending!")
            raise ConnectionResetError("Simulated response loss after commit")

        return op, 200

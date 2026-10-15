"""Startup Recovery Manager for PS-021.

Invoked upon agent adapter startup. Inspects durable database state, identifies
interrupted tasks, reconciles uncertain side effects, and safely resumes work
while preserving dialog correlation and idempotency guarantees.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from aiori.db import DatabaseManager
from aiori.schema import DialogMessage, DialogStatus
from aiori.side_effects import SideEffectExecutor

logger = logging.getLogger("aiori.recovery")


class RecoveryReport:
    """Summary of operations reconciled or resumed during startup."""

    def __init__(self):
        self.total_found: int = 0
        self.reconciled_existing_effects: int = 0
        self.resumed_and_completed: int = 0
        self.recovered_operations: List[Dict[str, Any]] = []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_found": self.total_found,
            "reconciled_existing_effects": self.reconciled_existing_effects,
            "resumed_and_completed": self.resumed_and_completed,
            "recovered_operations": self.recovered_operations,
        }


class StartupRecoveryManager:
    """Reconciles and recovers non-terminal dialog operations on system startup."""

    def __init__(self, db: DatabaseManager):
        self.db = db

    def run_recovery(self) -> RecoveryReport:
        """Execute the startup recovery protocol.

        1. Load persisted non-terminal operations.
        2. Inspect their durable state (side-effects ledger).
        3. Identify interrupted work.
        4. Reconcile operations whose side-effect outcome is uncertain.
        5. Resume only work that is safe to repeat.
        6. Return stored results for completed work.
        7. Preserve dialog and operation identities.
        """
        report = RecoveryReport()
        non_terminal_ops = self.db.get_non_terminal_operations()
        report.total_found = len(non_terminal_ops)

        if not non_terminal_ops:
            logger.info("Startup recovery complete: No non-terminal operations found.")
            return report

        logger.info(
            "Startup recovery initiated: Found %d non-terminal operation(s).",
            len(non_terminal_ops),
        )

        for op in non_terminal_ops:
            logger.info(
                "Inspecting interrupted task: dialog_id=%s, idempotency_key=%s, status=%s, version=%d",
                op.dialog_id,
                op.idempotency_key,
                op.status.value,
                op.version,
            )

            # Step 2: Inspect durable side effect state
            effects = self.db.get_side_effects(op.idempotency_key)
            side_effect_count = len(effects)

            with self.db.transaction() as conn:
                # Refresh current record to guarantee version currency
                current_op = self.db.get_operation(op.idempotency_key)
                if not current_op or current_op.status == DialogStatus.COMPLETED:
                    # Already completed by another worker or transaction
                    continue

                if side_effect_count > 0:
                    # Case 1: Side effect ALREADY committed, but operation record was not marked COMPLETED
                    logger.info(
                        "Reconciling task %s: Side effect already committed. Marking COMPLETED without re-execution.",
                        op.idempotency_key,
                    )
                    # First transition to RECOVERING if not already
                    if current_op.status != DialogStatus.RECOVERING:
                        current_op = self.db.update_operation_status(
                            conn=conn,
                            idempotency_key=current_op.idempotency_key,
                            expected_version=current_op.version,
                            target_status=DialogStatus.RECOVERING,
                            audit_details="Recovery: Marked RECOVERING for reconciliation",
                        )

                    # Reconstruct result from existing side effect
                    effect = effects[0]
                    reconciled_result = {
                        "reconciled": True,
                        "task_type": effect["task_type"],
                        "action": effect["action"],
                        "details": effect["details"],
                        "side_effect_id": effect["effect_id"],
                        "status": "SETTLED" if effect["task_type"] == "payment_transfer" else "CONFIRMED",
                    }

                    # Mark COMPLETED with preserved identity
                    final_op = self.db.update_operation_status(
                        conn=conn,
                        idempotency_key=current_op.idempotency_key,
                        expected_version=current_op.version,
                        target_status=DialogStatus.COMPLETED,
                        result=reconciled_result,
                        audit_details="Recovery: Reconciled committed side effect and completed task",
                    )
                    report.reconciled_existing_effects += 1
                    report.recovered_operations.append({
                        "dialog_id": final_op.dialog_id,
                        "idempotency_key": final_op.idempotency_key,
                        "action": "RECONCILED",
                        "status": final_op.status.value,
                        "side_effect_count": side_effect_count,
                    })

                else:
                    # Case 2: Side effect was NOT committed. Interrupted before side effect commit.
                    logger.info(
                        "Resuming interrupted task %s: No side effect committed. Transitioning through RECOVERING to execute.",
                        op.idempotency_key,
                    )
                    # Transition to RECOVERING
                    current_op = self.db.update_operation_status(
                        conn=conn,
                        idempotency_key=current_op.idempotency_key,
                        expected_version=current_op.version,
                        target_status=DialogStatus.RECOVERING,
                        audit_details="Recovery: Interrupted task marked RECOVERING",
                    )

                    # Transition RECOVERING -> RUNNING
                    current_op = self.db.update_operation_status(
                        conn=conn,
                        idempotency_key=current_op.idempotency_key,
                        expected_version=current_op.version,
                        target_status=DialogStatus.RUNNING,
                        audit_details="Recovery: Resuming execution",
                    )

                    # Execute deterministic side effect atomically
                    result = SideEffectExecutor.execute(conn=conn, db=self.db, msg=current_op)

                    # Transition RUNNING -> COMPLETED
                    final_op = self.db.update_operation_status(
                        conn=conn,
                        idempotency_key=current_op.idempotency_key,
                        expected_version=current_op.version,
                        target_status=DialogStatus.COMPLETED,
                        result=result,
                        audit_details="Recovery: Executed side effect and marked COMPLETED",
                    )
                    report.resumed_and_completed += 1
                    report.recovered_operations.append({
                        "dialog_id": final_op.dialog_id,
                        "idempotency_key": final_op.idempotency_key,
                        "action": "RESUMED_AND_COMPLETED",
                        "status": final_op.status.value,
                        "side_effect_count": self.db.count_side_effects(final_op.idempotency_key),
                    })

        return report

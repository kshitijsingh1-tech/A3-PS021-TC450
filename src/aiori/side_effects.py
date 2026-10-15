"""Deterministic Mock Side Effects Engine for PS-021.

Executes deterministic side effects within the caller's SQLite transaction
so that business state modification and operation completion remain atomic.
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any, Dict

from aiori.db import DatabaseManager
from aiori.schema import DialogMessage


class SideEffectExecutor:
    """Executes business logic side effects and records them in SQLite."""

    @classmethod
    def execute(
        cls,
        conn: sqlite3.Connection,
        db: DatabaseManager,
        msg: DialogMessage,
    ) -> Dict[str, Any]:
        """Execute the deterministic business action within the active transaction.

        Returns the computed result dictionary.
        """
        effect_id = f"eff_{uuid.uuid4().hex[:12]}"
        task_type = msg.task_type
        payload = msg.payload

        if task_type == "payment_transfer":
            account_id = payload.get("account_id", "acc_default")
            amount = float(payload.get("amount", 100.0))
            currency = payload.get("currency", "USD")
            action = "CREDIT_ACCOUNT"
            details = {
                "account_id": account_id,
                "amount": amount,
                "currency": currency,
            }
            result = {
                "transfer_id": f"tx_{msg.idempotency_key[:8]}",
                "account_id": account_id,
                "amount": amount,
                "currency": currency,
                "status": "SETTLED",
            }
        elif task_type == "reservation":
            resource_id = payload.get("resource_id", "room_101")
            holder = payload.get("holder", "guest")
            action = "BOOK_RESOURCE"
            details = {"resource_id": resource_id, "holder": holder}
            result = {
                "reservation_id": f"res_{msg.idempotency_key[:8]}",
                "resource_id": resource_id,
                "status": "CONFIRMED",
            }
        else:
            # Generic deterministic side effect
            action = "EXECUTE_GENERIC_TASK"
            details = {"task_type": task_type, "payload_keys": list(payload.keys())}
            result = {
                "task_id": f"task_{msg.idempotency_key[:8]}",
                "task_type": task_type,
                "status": "SUCCESS",
            }

        # Atomically record side effect in the ledger
        db.record_side_effect(
            conn=conn,
            effect_id=effect_id,
            idempotency_key=msg.idempotency_key,
            dialog_id=msg.dialog_id,
            task_type=task_type,
            action=action,
            details=details,
        )

        return result

"""Scenario 1: Request lost before delivery.

Simulates a client-side delivery failure where the initial message never reaches Adapter B.
Adapter A retries the task preserving dialog_id and idempotency_key while issuing a
new request_id and incrementing the attempt counter.

Assertions:
1. Final status is COMPLETED.
2. dialog_id and idempotency_key are preserved across retries.
3. attempt counter reflects retry count (2).
4. Side-effect is executed exactly once in durable storage.
"""

import uuid
from aiori.client import AdapterAClient
from aiori.db import DatabaseManager
from aiori.schema import DialogStatus
from tests.conftest import ServerProcessManager


def test_scenario_1_request_lost_before_delivery(
    running_server: ServerProcessManager,
    adapter_a: AdapterAClient,
):
    dialog_id = f"dlg_{uuid.uuid4().hex[:8]}"
    idempotency_key = f"key_{uuid.uuid4().hex[:8]}"
    task_type = "payment_transfer"
    payload = {"account_id": "acc_cust_123", "amount": 250.0, "currency": "USD"}

    # Simulate attempt 1 being lost / dropped locally before delivery
    # (e.g., connection failed or client timeout)
    attempt_1_msg = adapter_a.create_message(
        dialog_id=dialog_id,
        idempotency_key=idempotency_key,
        task_type=task_type,
        payload=payload,
        attempt=1,
    )
    # Notice: attempt_1_msg is never sent to Adapter B.

    # Adapter A retry logic initiates attempt 2 with a new request_id and attempt=2
    attempt_2_msg = adapter_a.create_message(
        dialog_id=dialog_id,
        idempotency_key=idempotency_key,
        task_type=task_type,
        payload=payload,
        attempt=2,
    )

    # Invariants before delivery
    assert attempt_1_msg.dialog_id == attempt_2_msg.dialog_id
    assert attempt_1_msg.idempotency_key == attempt_2_msg.idempotency_key
    assert attempt_1_msg.request_id != attempt_2_msg.request_id
    assert attempt_2_msg.attempt == 2

    # Adapter A sends attempt 2 to Adapter B
    resp = adapter_a.send_task(attempt_2_msg)

    # 1. Assert response status and identity preservation
    assert resp.status == DialogStatus.COMPLETED
    assert resp.dialog_id == dialog_id
    assert resp.idempotency_key == idempotency_key
    assert resp.request_id == attempt_2_msg.request_id
    assert resp.attempt == 2
    assert resp.result is not None
    assert resp.result["status"] == "SETTLED"
    assert resp.result["amount"] == 250.0

    # 2. Assert durable SQLite side-effect count is exactly 1
    db = DatabaseManager(running_server.db_path)
    side_effect_count = db.count_side_effects(idempotency_key)
    assert side_effect_count == 1, f"Expected 1 side effect, found {side_effect_count}"

    # 3. Assert operation record in DB matches expected status
    op_record = db.get_operation(idempotency_key)
    assert op_record is not None
    assert op_record.status == DialogStatus.COMPLETED
    assert op_record.attempt == 2

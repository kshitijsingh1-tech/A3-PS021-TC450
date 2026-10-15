"""Scenario 6: Idempotency key reuse with different semantic payload.

Tests that attempting to reuse an existing idempotency key with a different payload
is rejected with HTTP 409 Conflict and an IdempotencyConflictError, preventing
accidental mutation or semantic divergence.

Assertions:
1. First request with payload A succeeds with HTTP 200.
2. Second request with same idempotency key but payload B fails with HTTP 409 Conflict.
3. Third request with same idempotency key and payload A (matching original) succeeds with HTTP 200.
4. Total side effects remain 1.
"""

import uuid
import httpx
import pytest

from aiori.client import AdapterAClient
from aiori.db import DatabaseManager
from aiori.schema import DialogStatus
from tests.conftest import ServerProcessManager


def test_scenario_6_idempotency_conflict_rejection(running_server: ServerProcessManager):
    dialog_id = f"dlg_{uuid.uuid4().hex[:8]}"
    idempotency_key = f"key_conflict_{uuid.uuid4().hex[:8]}"
    task_type = "payment_transfer"

    client = AdapterAClient(base_url=running_server.base_url)

    payload_a = {"account_id": "acc_primary", "amount": 100.0}
    payload_b = {"account_id": "acc_primary", "amount": 999.0}  # Conflict: different amount!

    # 1. Send first request with payload A
    msg_1 = client.create_message(
        dialog_id=dialog_id,
        idempotency_key=idempotency_key,
        task_type=task_type,
        payload=payload_a,
        attempt=1,
    )
    resp_1 = client.send_task(msg_1)
    assert resp_1.status == DialogStatus.COMPLETED
    assert resp_1.result["amount"] == 100.0

    # 2. Send conflicting request with same idempotency_key but payload B
    msg_2 = client.create_message(
        dialog_id=dialog_id,
        idempotency_key=idempotency_key,
        task_type=task_type,
        payload=payload_b,
        attempt=2,
    )

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        client.send_task(msg_2)

    assert exc_info.value.response.status_code == 409
    assert "IdempotencyConflictError" in exc_info.value.response.text
    assert "different payload" in exc_info.value.response.text

    # 3. Retry with original payload A succeeds normally with stored result
    msg_3 = client.create_message(
        dialog_id=dialog_id,
        idempotency_key=idempotency_key,
        task_type=task_type,
        payload=payload_a,
        attempt=3,
    )
    resp_3 = client.send_task(msg_3)
    assert resp_3.status == DialogStatus.COMPLETED
    assert resp_3.result["amount"] == 100.0

    # 4. Total side effects in database is strictly 1
    db = DatabaseManager(running_server.db_path)
    assert db.count_side_effects(idempotency_key) == 1

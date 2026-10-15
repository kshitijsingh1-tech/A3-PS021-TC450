"""Scenario 3: Response lost after successful commit.

Simulates the classic distributed systems challenge: the server successfully executes
the business side effect and commits COMPLETED to durable storage, but the response
is lost (e.g., process crashes post-commit or network connection resets before delivery).
Adapter A retries the task. Adapter B identifies the completed operation, suppresses
duplicate side-effect execution, and returns the stored result.

Assertions:
1. First attempt fails on client side due to lost response/connection drop.
2. Server durable database has committed side effect and COMPLETED state.
3. Second attempt succeeds and receives stored result.
4. Durable side-effect execution count is strictly 1 (no double execution).
5. Dialog and operation identities are preserved.
"""

import time
import uuid
import httpx
import pytest

from aiori.client import AdapterAClient
from aiori.db import DatabaseManager
from aiori.schema import DialogStatus
from tests.conftest import ServerProcessManager, find_free_port


def test_scenario_3_response_lost_after_successful_commit(temp_db):
    port = find_free_port()
    server_mgr = ServerProcessManager(temp_db, port)
    server_mgr.start()

    dialog_id = f"dlg_{uuid.uuid4().hex[:8]}"
    idempotency_key = f"key_{uuid.uuid4().hex[:8]}"
    task_type = "payment_transfer"
    payload = {"account_id": "acc_merchant_888", "amount": 1250.0, "currency": "USD"}

    client = AdapterAClient(base_url=server_mgr.base_url)

    # 1. Attempt 1: Server executes and commits, but crashes immediately before response
    attempt_1 = client.create_message(
        dialog_id=dialog_id,
        idempotency_key=idempotency_key,
        task_type=task_type,
        payload=payload,
        attempt=1,
    )

    with pytest.raises((httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError)):
        client.send_task(attempt_1, fault_action="crash_after_commit")

    time.sleep(0.3)
    assert not server_mgr.is_alive(), "Server process should have terminated after commit."

    # 2. Verify state on disk before client retry
    db = DatabaseManager(temp_db)
    op_record = db.get_operation(idempotency_key)
    assert op_record is not None
    assert op_record.status == DialogStatus.COMPLETED
    assert op_record.result is not None
    stored_result = op_record.result

    # Side-effect was committed once
    assert db.count_side_effects(idempotency_key) == 1

    # 3. Restart server on the same database
    new_port = find_free_port()
    new_server_mgr = ServerProcessManager(temp_db, new_port)
    try:
        new_server_mgr.start()
        new_client = AdapterAClient(base_url=new_server_mgr.base_url)

        # 4. Attempt 2: Client retries the same logical operation (new request_id, attempt=2)
        attempt_2 = new_client.create_message(
            dialog_id=dialog_id,
            idempotency_key=idempotency_key,
            task_type=task_type,
            payload=payload,
            attempt=2,
        )

        resp_2 = new_client.send_task(attempt_2)

        # 5. Assertions on response and deduplication
        assert resp_2.status == DialogStatus.COMPLETED
        assert resp_2.dialog_id == dialog_id
        assert resp_2.idempotency_key == idempotency_key
        assert resp_2.result == stored_result

        # 6. Verify side-effect execution count is STILL strictly 1
        assert db.count_side_effects(idempotency_key) == 1

    finally:
        new_server_mgr.kill()

"""Scenario 2: Process crash during execution.

Simulates a real server process termination during task execution before completion.
Adapter B is abruptly killed mid-flight. A new Adapter B process is then launched
pointing to the exact same SQLite database. The Startup Recovery Manager recovers
the interrupted task safely without duplicate side effects.

Assertions:
1. Server process genuinely terminates mid-execution.
2. New server process recovers upon startup.
3. Durable side-effect count is exactly 1 (not duplicated).
4. Operation finishes in COMPLETED state with valid result.
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


def test_scenario_2_crash_during_execution_and_startup_recovery(temp_db):
    port = find_free_port()
    server_mgr = ServerProcessManager(temp_db, port)
    server_mgr.start()

    dialog_id = f"dlg_{uuid.uuid4().hex[:8]}"
    idempotency_key = f"key_{uuid.uuid4().hex[:8]}"
    task_type = "payment_transfer"
    payload = {"account_id": "acc_user_456", "amount": 500.0, "currency": "USD"}

    client = AdapterAClient(base_url=server_mgr.base_url)

    # 1. Send request with fault injection trigger to crash before side effect commit
    msg = client.create_message(
        dialog_id=dialog_id,
        idempotency_key=idempotency_key,
        task_type=task_type,
        payload=payload,
        attempt=1,
    )

    with pytest.raises((httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError)):
        # Server process will os._exit(42) mid-execution
        client.send_task(msg, fault_action="crash_before_execution")

    # Give OS a moment to reap process
    time.sleep(0.3)

    # 2. Assert server process genuinely died
    assert not server_mgr.is_alive(), "Server process was expected to be terminated by fault injection."

    # Inspect DB immediately after crash: task was registered (ACCEPTED/RUNNING), but 0 side effects
    db_before_restart = DatabaseManager(temp_db)
    op_before = db_before_restart.get_operation(idempotency_key)
    assert op_before is not None, "Operation should have been durably registered before crash."
    assert op_before.status in {DialogStatus.ACCEPTED, DialogStatus.RUNNING}
    assert db_before_restart.count_side_effects(idempotency_key) == 0

    # 3. Start a brand new Adapter B server process reusing the exact same SQLite database
    new_port = find_free_port()
    new_server_mgr = ServerProcessManager(temp_db, new_port)
    try:
        new_server_mgr.start()

        # Check startup recovery report
        new_client = AdapterAClient(base_url=new_server_mgr.base_url)
        recovery_status = new_client.http_client.get("/recovery/status").json()
        assert recovery_status["total_found"] >= 1
        assert recovery_status["resumed_and_completed"] >= 1

        # 4. Query operation state via new server
        op_resp = new_client.get_operation(idempotency_key)
        assert op_resp.status == DialogStatus.COMPLETED
        assert op_resp.dialog_id == dialog_id
        assert op_resp.idempotency_key == idempotency_key
        assert op_resp.result is not None
        assert op_resp.result["status"] == "SETTLED"
        assert op_resp.result["amount"] == 500.0

        # 5. Assert side-effect count in durable database is EXACTLY 1
        db_after = DatabaseManager(temp_db)
        side_effect_count = db_after.count_side_effects(idempotency_key)
        assert side_effect_count == 1, f"Expected exactly 1 side effect after recovery, got {side_effect_count}"

    finally:
        new_server_mgr.kill()

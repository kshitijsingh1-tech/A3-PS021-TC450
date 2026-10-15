"""Scenario 5: Restart with a mixture of completed and pending tasks.

Tests recovery resilience when a server restarts with a diverse set of persisted tasks:
- Task 1: Already COMPLETED prior to crash (side effect committed: 1).
- Task 2: Interrupted RUNNING with NO committed side effect (side effect committed: 0).
- Task 3: Interrupted RUNNING where side effect WAS committed in ledger, but process died before completion status (side effect committed: 1).
- Task 4: Interrupted in ACCEPTED state before execution (side effect committed: 0).

Upon restart:
- Task 1 remains COMPLETED with preserved result.
- Task 2 is safely resumed and completed (side effect count -> 1).
- Task 3 is reconciled without re-executing (side effect count stays 1).
- Task 4 is safely resumed and completed (side effect count -> 1).
All tasks end in COMPLETED, each with exactly 1 durable side effect.
"""

import json
import uuid
import httpx

from aiori.client import AdapterAClient
from aiori.db import DatabaseManager
from aiori.schema import DialogMessage, DialogStatus, get_utc_now_iso
from tests.conftest import ServerProcessManager, find_free_port


def test_scenario_5_mixed_tasks_startup_recovery(temp_db):
    port = find_free_port()
    server_mgr = ServerProcessManager(temp_db, port)
    server_mgr.start()

    client = AdapterAClient(base_url=server_mgr.base_url)

    # 1. Task 1: Normal completed task via live server
    t1_dialog = f"dlg_{uuid.uuid4().hex[:8]}"
    t1_key = f"key_t1_{uuid.uuid4().hex[:8]}"
    t1_msg = client.create_message(
        dialog_id=t1_dialog,
        idempotency_key=t1_key,
        task_type="payment_transfer",
        payload={"account_id": "acc_1", "amount": 100.0},
        attempt=1,
    )
    t1_resp = client.send_task(t1_msg)
    assert t1_resp.status == DialogStatus.COMPLETED

    # Now simulate the crash of server 1
    server_mgr.kill()

    # 2. Directly seed tasks 2, 3, 4 into durable storage to simulate varied crash states
    db = DatabaseManager(temp_db)
    now = get_utc_now_iso()

    # Task 2: RUNNING with 0 side effects
    t2_dialog = f"dlg_{uuid.uuid4().hex[:8]}"
    t2_key = f"key_t2_{uuid.uuid4().hex[:8]}"
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO dialogs VALUES (?, ?, ?, ?, ?)",
            (t2_dialog, "1.0.0-exp", "RUNNING", now, now),
        )
        conn.execute(
            """
            INSERT INTO operations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                t2_key,
                t2_dialog,
                "req_t2_1",
                "adapter_a",
                "adapter_b",
                "payment_transfer",
                json.dumps({"account_id": "acc_2", "amount": 200.0}),
                "hash_t2",
                DialogStatus.RUNNING.value,
                1,
                None,
                None,
                2,
                now,
                now,
            ),
        )

    # Task 3: RUNNING with 1 committed side effect in ledger
    t3_dialog = f"dlg_{uuid.uuid4().hex[:8]}"
    t3_key = f"key_t3_{uuid.uuid4().hex[:8]}"
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO dialogs VALUES (?, ?, ?, ?, ?)",
            (t3_dialog, "1.0.0-exp", "RUNNING", now, now),
        )
        conn.execute(
            """
            INSERT INTO operations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                t3_key,
                t3_dialog,
                "req_t3_1",
                "adapter_a",
                "adapter_b",
                "payment_transfer",
                json.dumps({"account_id": "acc_3", "amount": 300.0}),
                "hash_t3",
                DialogStatus.RUNNING.value,
                1,
                None,
                None,
                2,
                now,
                now,
            ),
        )
        # Side effect committed into side_effects table
        conn.execute(
            """
            INSERT INTO side_effects VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "eff_seeded_3",
                t3_key,
                t3_dialog,
                "payment_transfer",
                "CREDIT_ACCOUNT",
                json.dumps({"account_id": "acc_3", "amount": 300.0}),
                now,
            ),
        )

    # Task 4: ACCEPTED with 0 side effects
    t4_dialog = f"dlg_{uuid.uuid4().hex[:8]}"
    t4_key = f"key_t4_{uuid.uuid4().hex[:8]}"
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO dialogs VALUES (?, ?, ?, ?, ?)",
            (t4_dialog, "1.0.0-exp", "ACCEPTED", now, now),
        )
        conn.execute(
            """
            INSERT INTO operations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                t4_key,
                t4_dialog,
                "req_t4_1",
                "adapter_a",
                "adapter_b",
                "payment_transfer",
                json.dumps({"account_id": "acc_4", "amount": 400.0}),
                "hash_t4",
                DialogStatus.ACCEPTED.value,
                1,
                None,
                None,
                2,
                now,
                now,
            ),
        )

    # 3. Boot new server process reusing the SQLite database
    new_port = find_free_port()
    new_server = ServerProcessManager(temp_db, new_port)
    try:
        new_server.start()
        new_client = AdapterAClient(base_url=new_server.base_url)

        # Inspect Recovery Report
        rec_status = new_client.http_client.get("/recovery/status").json()
        assert rec_status["total_found"] == 3
        assert rec_status["reconciled_existing_effects"] == 1  # Task 3
        assert rec_status["resumed_and_completed"] == 2        # Tasks 2 & 4

        # 4. Check all four operations via client
        for key, exp_amount in [
            (t1_key, 100.0),
            (t2_key, 200.0),
            (t3_key, 300.0),
            (t4_key, 400.0),
        ]:
            op = new_client.get_operation(key)
            assert op.status == DialogStatus.COMPLETED
            assert op.result is not None
            # Check side effect count in DB is strictly 1 for EACH task
            cnt = db.count_side_effects(key)
            assert cnt == 1, f"Task {key} has {cnt} side effects, expected 1"

        # Total side effects across entire system = 4
        with db.get_connection() as conn:
            total_effects = conn.execute("SELECT COUNT(*) as c FROM side_effects").fetchone()["c"]
            assert total_effects == 4

    finally:
        new_server.kill()

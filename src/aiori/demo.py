"""Demo Runner for PS-021: Experimental Dialog Correlation and Recovery.

Executes all fault-injection and recovery scenarios end-to-end,
asserts durable invariants, and prints a concise execution report.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import shutil
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import httpx

from aiori.client import AdapterAClient
from aiori.db import DatabaseManager
from aiori.schema import DialogStatus, get_utc_now_iso
from tests.conftest import ServerProcessManager, find_free_port


@dataclass
class DemoScenarioResult:
    scenario_name: str
    dialog_id: str
    final_status: str
    identity_preserved: bool
    side_effects_count: int
    recovery_passed: bool
    details: str


def run_scenario_1(temp_dir: Path) -> DemoScenarioResult:
    """Scenario 1: Request lost before delivery."""
    db_path = temp_dir / "s1.db"
    port = find_free_port()
    server = ServerProcessManager(db_path, port)
    server.start()
    try:
        dialog_id = f"dlg_s1_{uuid.uuid4().hex[:6]}"
        idemp_key = f"key_s1_{uuid.uuid4().hex[:6]}"
        client = AdapterAClient(base_url=server.base_url)

        # Attempt 1: dropped client-side
        # Attempt 2: sent with attempt=2, fresh req_id, same idemp_key & dialog_id
        msg2 = client.create_message(
            dialog_id=dialog_id,
            idempotency_key=idemp_key,
            task_type="payment_transfer",
            payload={"account": "demo_user_1", "amount": 100.0},
            attempt=2,
        )
        resp = client.send_task(msg2)

        db = DatabaseManager(db_path)
        effects = db.count_side_effects(idemp_key)
        preserved = resp.dialog_id == dialog_id and resp.idempotency_key == idemp_key
        passed = resp.status == DialogStatus.COMPLETED and effects == 1 and preserved

        return DemoScenarioResult(
            scenario_name="1. Lost Request Before Delivery",
            dialog_id=dialog_id,
            final_status=resp.status.value,
            identity_preserved=preserved,
            side_effects_count=effects,
            recovery_passed=passed,
            details="Retried with attempt=2, single side effect committed",
        )
    finally:
        server.kill()


def run_scenario_2(temp_dir: Path) -> DemoScenarioResult:
    """Scenario 2: Process crash during execution."""
    db_path = temp_dir / "s2.db"
    port = find_free_port()
    server1 = ServerProcessManager(db_path, port)
    server1.start()

    dialog_id = f"dlg_s2_{uuid.uuid4().hex[:6]}"
    idemp_key = f"key_s2_{uuid.uuid4().hex[:6]}"
    client1 = AdapterAClient(base_url=server1.base_url)

    msg = client1.create_message(
        dialog_id=dialog_id,
        idempotency_key=idemp_key,
        task_type="payment_transfer",
        payload={"account": "demo_user_2", "amount": 200.0},
        attempt=1,
    )

    try:
        client1.send_task(msg, fault_action="crash_before_execution")
    except Exception:
        pass  # Expected process crash

    time.sleep(0.3)
    server1.kill()

    # Restart server on the same database
    new_port = find_free_port()
    server2 = ServerProcessManager(db_path, new_port)
    server2.start()
    try:
        client2 = AdapterAClient(base_url=server2.base_url)
        op = client2.get_operation(idemp_key)

        db = DatabaseManager(db_path)
        effects = db.count_side_effects(idemp_key)
        preserved = op.dialog_id == dialog_id and op.idempotency_key == idemp_key
        passed = op.status == DialogStatus.COMPLETED and effects == 1 and preserved

        return DemoScenarioResult(
            scenario_name="2. Crash Mid-Execution & Startup Recovery",
            dialog_id=dialog_id,
            final_status=op.status.value,
            identity_preserved=preserved,
            side_effects_count=effects,
            recovery_passed=passed,
            details="Recovered uncommitted task upon new process boot",
        )
    finally:
        server2.kill()


def run_scenario_3(temp_dir: Path) -> DemoScenarioResult:
    """Scenario 3: Response lost after successful commit."""
    db_path = temp_dir / "s3.db"
    port = find_free_port()
    server1 = ServerProcessManager(db_path, port)
    server1.start()

    dialog_id = f"dlg_s3_{uuid.uuid4().hex[:6]}"
    idemp_key = f"key_s3_{uuid.uuid4().hex[:6]}"
    client1 = AdapterAClient(base_url=server1.base_url)

    msg1 = client1.create_message(
        dialog_id=dialog_id,
        idempotency_key=idemp_key,
        task_type="payment_transfer",
        payload={"account": "demo_user_3", "amount": 300.0},
        attempt=1,
    )

    try:
        client1.send_task(msg1, fault_action="crash_after_commit")
    except Exception:
        pass  # Expected crash after DB commit

    time.sleep(0.3)
    server1.kill()

    # Restart server on same DB
    new_port = find_free_port()
    server2 = ServerProcessManager(db_path, new_port)
    server2.start()
    try:
        client2 = AdapterAClient(base_url=server2.base_url)
        msg2 = client2.create_message(
            dialog_id=dialog_id,
            idempotency_key=idemp_key,
            task_type="payment_transfer",
            payload={"account": "demo_user_3", "amount": 300.0},
            attempt=2,
        )
        resp2 = client2.send_task(msg2)

        db = DatabaseManager(db_path)
        effects = db.count_side_effects(idemp_key)
        preserved = resp2.dialog_id == dialog_id and resp2.idempotency_key == idemp_key
        passed = resp2.status == DialogStatus.COMPLETED and effects == 1 and preserved

        return DemoScenarioResult(
            scenario_name="3. Response Lost Post-Commit & Deduplication",
            dialog_id=dialog_id,
            final_status=resp2.status.value,
            identity_preserved=preserved,
            side_effects_count=effects,
            recovery_passed=passed,
            details="Returned stored result on retry without repeating effect",
        )
    finally:
        server2.kill()


def run_scenario_4(temp_dir: Path) -> DemoScenarioResult:
    """Scenario 4: Concurrent duplicate requests."""
    db_path = temp_dir / "s4.db"
    port = find_free_port()
    server = ServerProcessManager(db_path, port)
    server.start()
    try:
        dialog_id = f"dlg_s4_{uuid.uuid4().hex[:6]}"
        idemp_key = f"key_s4_{uuid.uuid4().hex[:6]}"
        payload = {"account": "demo_user_4", "amount": 400.0}

        def worker():
            c = AdapterAClient(base_url=server.base_url)
            try:
                m = c.create_message(
                    dialog_id=dialog_id,
                    idempotency_key=idemp_key,
                    task_type="payment_transfer",
                    payload=payload,
                )
                r = c.http_client.post("/dialog/task", json=m.model_dump())
                return r.status_code
            finally:
                c.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
            codes = list(ex.map(lambda _: worker(), range(6)))

        client = AdapterAClient(base_url=server.base_url)
        final_op = client.get_operation(idemp_key)

        db = DatabaseManager(db_path)
        effects = db.count_side_effects(idemp_key)
        preserved = final_op.dialog_id == dialog_id and final_op.idempotency_key == idemp_key
        passed = (
            final_op.status == DialogStatus.COMPLETED
            and effects == 1
            and preserved
            and all(c in {200, 202} for c in codes)
        )

        return DemoScenarioResult(
            scenario_name="4. Concurrent Duplicate Serialization",
            dialog_id=dialog_id,
            final_status=final_op.status.value,
            identity_preserved=preserved,
            side_effects_count=effects,
            recovery_passed=passed,
            details="6 concurrent requests serialized, exactly 1 side effect",
        )
    finally:
        server.kill()


def run_scenario_5(temp_dir: Path) -> DemoScenarioResult:
    """Scenario 5: Restart with mixture of completed and pending tasks.

    Tests full 4-task recovery spectrum:
    - Task 1: COMPLETED prior to crash (1 side effect).
    - Task 2: Interrupted RUNNING with 0 side effects -> resumed (1 side effect).
    - Task 3: Interrupted RUNNING with 1 committed side effect in ledger -> reconciled (1 side effect).
    - Task 4: Interrupted ACCEPTED with 0 side effects -> resumed (1 side effect).
    """
    db_path = temp_dir / "s5.db"
    port = find_free_port()
    server1 = ServerProcessManager(db_path, port)
    server1.start()

    dialog_id = f"dlg_s5_{uuid.uuid4().hex[:6]}"
    c1 = AdapterAClient(base_url=server1.base_url)

    # 1. Complete Task 1 via live server
    k1 = f"key_m1_{uuid.uuid4().hex[:6]}"
    m1 = c1.create_message(
        dialog_id=dialog_id,
        idempotency_key=k1,
        task_type="payment_transfer",
        payload={"account": "user_1", "amount": 10.0},
    )
    c1.send_task(m1)
    server1.kill()

    # 2. Seed pending tasks 2, 3, 4 directly into SQLite
    db = DatabaseManager(db_path)
    now = get_utc_now_iso()

    # Task 2: RUNNING with 0 side effects
    k2 = f"key_m2_{uuid.uuid4().hex[:6]}"
    # Task 3: RUNNING with 1 committed side effect
    k3 = f"key_m3_{uuid.uuid4().hex[:6]}"
    # Task 4: ACCEPTED with 0 side effects
    k4 = f"key_m4_{uuid.uuid4().hex[:6]}"

    with db.transaction() as conn:
        conn.execute("INSERT OR IGNORE INTO dialogs VALUES (?, ?, ?, ?, ?)", (dialog_id, "1.0.0-exp", "RUNNING", now, now))
        # Task 2
        conn.execute(
            "INSERT INTO operations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (k2, dialog_id, "req2", "adapter_a", "adapter_b", "payment_transfer", json.dumps({"account": "user_2", "amount": 20.0}), "h2", "RUNNING", 1, None, None, 2, now, now),
        )
        # Task 3
        conn.execute(
            "INSERT INTO operations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (k3, dialog_id, "req3", "adapter_a", "adapter_b", "payment_transfer", json.dumps({"account": "user_3", "amount": 30.0}), "h3", "RUNNING", 1, None, None, 2, now, now),
        )
        conn.execute(
            "INSERT INTO side_effects VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("eff_demo_3", k3, dialog_id, "payment_transfer", "CREDIT_ACCOUNT", json.dumps({"account": "user_3", "amount": 30.0}), now),
        )
        # Task 4
        conn.execute(
            "INSERT INTO operations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (k4, dialog_id, "req4", "adapter_a", "adapter_b", "payment_transfer", json.dumps({"account": "user_4", "amount": 40.0}), "h4", "ACCEPTED", 1, None, None, 2, now, now),
        )

    # 3. Boot new server on the same SQLite database
    new_port = find_free_port()
    server2 = ServerProcessManager(db_path, new_port)
    server2.start()
    try:
        c2 = AdapterAClient(base_url=server2.base_url)
        op1 = c2.get_operation(k1)
        op2 = c2.get_operation(k2)
        op3 = c2.get_operation(k3)
        op4 = c2.get_operation(k4)

        effects1 = db.count_side_effects(k1)
        effects2 = db.count_side_effects(k2)
        effects3 = db.count_side_effects(k3)
        effects4 = db.count_side_effects(k4)
        total_effects = effects1 + effects2 + effects3 + effects4

        all_completed = all(op.status == DialogStatus.COMPLETED for op in [op1, op2, op3, op4])
        all_preserved = all(op.dialog_id == dialog_id for op in [op1, op2, op3, op4])
        all_effects_exact = all(cnt == 1 for cnt in [effects1, effects2, effects3, effects4])

        passed = all_completed and all_preserved and all_effects_exact and total_effects == 4

        return DemoScenarioResult(
            scenario_name="5. Mixed Completed & Pending Recovery",
            dialog_id=dialog_id,
            final_status="COMPLETED (4/4)",
            identity_preserved=all_preserved,
            side_effects_count=total_effects,
            recovery_passed=passed,
            details="4 tasks recovered (1 completed, 2 resumed, 1 reconciled), 4 total effects",
        )
    finally:
        server2.kill()


def run_scenario_6(temp_dir: Path) -> DemoScenarioResult:
    """Scenario 6: Reuse of idempotency key with different payload -> 409 Conflict."""
    db_path = temp_dir / "s6.db"
    port = find_free_port()
    server = ServerProcessManager(db_path, port)
    server.start()
    try:
        dialog_id = f"dlg_s6_{uuid.uuid4().hex[:6]}"
        idemp_key = f"key_s6_{uuid.uuid4().hex[:6]}"
        client = AdapterAClient(base_url=server.base_url)

        m1 = client.create_message(
            dialog_id=dialog_id,
            idempotency_key=idemp_key,
            task_type="payment_transfer",
            payload={"amount": 100.0},
        )
        r1 = client.send_task(m1)

        # Send conflicting payload
        m2 = client.create_message(
            dialog_id=dialog_id,
            idempotency_key=idemp_key,
            task_type="payment_transfer",
            payload={"amount": 999.0},  # Different!
        )
        conflict_status = None
        try:
            client.send_task(m2)
        except httpx.HTTPStatusError as e:
            conflict_status = e.response.status_code

        db = DatabaseManager(db_path)
        effects = db.count_side_effects(idemp_key)
        passed = r1.status == DialogStatus.COMPLETED and conflict_status == 409 and effects == 1

        return DemoScenarioResult(
            scenario_name="6. Idempotency Key Payload Conflict",
            dialog_id=dialog_id,
            final_status="409 CONFLICT",
            identity_preserved=True,
            side_effects_count=effects,
            recovery_passed=passed,
            details="Rejected altered payload with HTTP 409, kept original side effect",
        )
    finally:
        server.kill()


def print_report(results: List[DemoScenarioResult]):
    """Format and print the demo report."""
    print("\n" + "=" * 92)
    print("       PS-021: DIALOG CORRELATION & STATE RECOVERY DEMONSTRATION REPORT       ")
    print("=" * 92)
    header = f"{'Scenario Name':<38} | {'Dialog ID':<14} | {'Status':<15} | {'Preserved':<9} | {'Effects':<7} | {'Result':<6}"
    print(header)
    print("-" * 92)

    all_passed = True
    for r in results:
        status_color = "PASS" if r.recovery_passed else "FAIL"
        if not r.recovery_passed:
            all_passed = False
        preserved_str = "YES" if r.identity_preserved else "NO"
        print(
            f"{r.scenario_name:<38} | {r.dialog_id:<14} | {r.final_status:<15} | {preserved_str:<9} | {r.side_effects_count:<7} | {status_color:<6}"
        )
        print(f"  -> Details: {r.details}")
        print("-" * 92)

    print("\nSummary:")
    passed_count = sum(1 for r in results if r.recovery_passed)
    print(f"Total Scenarios Executed: {len(results)}")
    print(f"Passed: {passed_count} / {len(results)}")
    print(f"System Recovery Verification: {'ALL TESTS PASSED' if all_passed else 'SOME TESTS FAILED'}\n")


def main():
    temp_dir = Path(tempfile.mkdtemp(prefix="aiori_demo_"))
    try:
        print("Executing PS-021 Automated Fault-Injection & Recovery Scenarios...")
        results = [
            run_scenario_1(temp_dir),
            run_scenario_2(temp_dir),
            run_scenario_3(temp_dir),
            run_scenario_4(temp_dir),
            run_scenario_5(temp_dir),
            run_scenario_6(temp_dir),
        ]
        print_report(results)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()

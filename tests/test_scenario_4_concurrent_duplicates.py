"""Scenario 4: Concurrent duplicate requests.

Simulates multiple concurrent threads/clients submitting the exact same task
simultaneously with identical idempotency keys.
Adapter B uses database uniqueness constraints and atomic transactions to serialize
registration and guarantee that the business side effect is executed strictly once.

Assertions:
1. All concurrent requests receive valid HTTP responses (200 OK or 202 In-Progress).
2. Side-effect is executed exactly once in durable storage.
3. No race conditions produce duplicate ledger entries or database corruption.
4. Final state is COMPLETED with correct result.
"""

import concurrent.futures
import uuid
import httpx

from aiori.client import AdapterAClient
from aiori.db import DatabaseManager
from aiori.schema import DialogStatus
from tests.conftest import ServerProcessManager


def test_scenario_4_concurrent_duplicate_requests(running_server: ServerProcessManager):
    dialog_id = f"dlg_{uuid.uuid4().hex[:8]}"
    idempotency_key = f"key_{uuid.uuid4().hex[:8]}"
    task_type = "payment_transfer"
    payload = {"account_id": "acc_concurrent_99", "amount": 750.0, "currency": "USD"}

    client = AdapterAClient(base_url=running_server.base_url)

    # Prepare 6 duplicate requests sent concurrently
    def fire_request(req_idx: int):
        thread_client = AdapterAClient(base_url=running_server.base_url)
        try:
            msg = thread_client.create_message(
                dialog_id=dialog_id,
                idempotency_key=idempotency_key,
                task_type=task_type,
                payload=payload,
                attempt=1,
            )
            # Send directly using httpx to observe raw response status
            resp = thread_client.http_client.post("/dialog/task", json=msg.model_dump())
            return req_idx, resp.status_code, resp.json()
        finally:
            thread_client.close()

    num_concurrent = 6
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_concurrent) as executor:
        futures = [executor.submit(fire_request, i) for i in range(num_concurrent)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    # 1. Assert all responses were successful (either 200 completed or 202 in-progress)
    status_codes = [r[1] for r in results]
    assert all(code in {200, 202} for code in status_codes), f"Unexpected status codes: {status_codes}"

    # 2. Wait for completion or query final operation status
    final_op = client.get_operation(idempotency_key)
    assert final_op.status == DialogStatus.COMPLETED
    assert final_op.result is not None
    assert final_op.result["amount"] == 750.0

    # 3. CRITICAL ASSERTION: Durable side-effect execution count is strictly 1
    db = DatabaseManager(running_server.db_path)
    side_effect_count = db.count_side_effects(idempotency_key)
    assert side_effect_count == 1, (
        f"Race condition detected! Side effect executed {side_effect_count} times instead of 1."
    )

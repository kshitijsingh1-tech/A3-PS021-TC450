"""Adapter A: Client-side Mock Agent Adapter for PS-021.

Manages outgoing dialog messages, correlation tracking, stable idempotency keys,
dynamic request IDs per transmission attempt, retry policies, and timeout handling.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, Optional

import httpx

from aiori.schema import (
    DialogMessage,
    DialogStatus,
    DialogStatusResponse,
    get_utc_now_iso,
)

logger = logging.getLogger("aiori.adapter_a")


class AdapterAClient:
    """Mock agent adapter representing the requesting party."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        adapter_id: str = "adapter_a",
        target_adapter: str = "adapter_b",
        timeout: float = 5.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.adapter_id = adapter_id
        self.target_adapter = target_adapter
        self.timeout = timeout
        self.http_client = httpx.Client(base_url=self.base_url, timeout=self.timeout)

    def close(self):
        self.http_client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def create_message(
        self,
        dialog_id: str,
        idempotency_key: str,
        task_type: str,
        payload: Dict[str, Any],
        attempt: int = 1,
    ) -> DialogMessage:
        """Create a new DialogMessage envelope with a fresh request_id but stable dialog/idempotency keys."""
        request_id = f"req_{uuid.uuid4().hex[:12]}"
        now = get_utc_now_iso()
        return DialogMessage(
            dialog_id=dialog_id,
            request_id=request_id,
            idempotency_key=idempotency_key,
            source_adapter=self.adapter_id,
            target_adapter=self.target_adapter,
            task_type=task_type,
            payload=payload,
            attempt=attempt,
            created_at=now,
            updated_at=now,
        )

    def send_task(
        self,
        msg: DialogMessage,
        fault_action: Optional[str] = None,
    ) -> DialogStatusResponse:
        """Send a single transmission attempt of a task to Adapter B.

        Raises httpx.HTTPError or httpx.HTTPStatusError on failures.
        """
        headers = {}
        if fault_action:
            headers["X-Fault-Action"] = fault_action

        resp = self.http_client.post(
            "/dialog/task",
            json=msg.model_dump(),
            headers=headers,
        )
        resp.raise_for_status()
        return DialogStatusResponse.model_validate(resp.json())

    def send_with_retry(
        self,
        dialog_id: str,
        idempotency_key: str,
        task_type: str,
        payload: Dict[str, Any],
        max_attempts: int = 3,
        backoff_sec: float = 0.5,
        fault_action_on_first_attempt: Optional[str] = None,
    ) -> DialogStatusResponse:
        """Send a task with automated client-side retry logic.

        Preserves dialog_id and idempotency_key across all attempts, while generating
        a new request_id and incrementing the attempt counter for each try.
        """
        last_exception: Optional[Exception] = None

        for attempt in range(1, max_attempts + 1):
            msg = self.create_message(
                dialog_id=dialog_id,
                idempotency_key=idempotency_key,
                task_type=task_type,
                payload=payload,
                attempt=attempt,
            )

            # Apply fault action only on first attempt if specified
            fault_action = fault_action_on_first_attempt if attempt == 1 else None

            logger.info(
                "Adapter A sending task: dialog_id=%s, idemp=%s, req_id=%s, attempt=%d",
                dialog_id,
                idempotency_key,
                msg.request_id,
                attempt,
            )

            try:
                response = self.send_task(msg, fault_action=fault_action)
                if response.status in {DialogStatus.COMPLETED, DialogStatus.ACCEPTED}:
                    return response
            except (httpx.RequestError, httpx.HTTPStatusError) as exc:
                logger.warning(
                    "Adapter A attempt %d failed: %s (%s). Retrying in %.2fs...",
                    attempt,
                    type(exc).__name__,
                    str(exc),
                    backoff_sec,
                )
                last_exception = exc
                if attempt < max_attempts:
                    time.sleep(backoff_sec)

        raise RuntimeError(
            f"Failed after {max_attempts} attempts for task {idempotency_key}. Last error: {last_exception}"
        )

    def get_dialog_status(self, dialog_id: str) -> Dict[str, Any]:
        """Query Adapter B for dialog status."""
        resp = self.http_client.get(f"/dialog/{dialog_id}/status")
        resp.raise_for_status()
        return resp.json()

    def get_operation(self, idempotency_key: str) -> DialogStatusResponse:
        """Query Adapter B for a specific operation's state and result."""
        resp = self.http_client.get(f"/dialog/operation/{idempotency_key}")
        resp.raise_for_status()
        return DialogStatusResponse.model_validate(resp.json())

    def get_side_effects(self, idempotency_key: str) -> Dict[str, Any]:
        """Query Adapter B for committed side effects count and details."""
        resp = self.http_client.get(f"/dialog/operation/{idempotency_key}/side-effects")
        resp.raise_for_status()
        return resp.json()

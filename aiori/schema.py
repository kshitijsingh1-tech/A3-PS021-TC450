"""Experimental Dialog Correlation & State Recovery Schema (PS-021).

NOTE: This schema is EXPERIMENTAL. It does not claim official IETF standard
compliance, universal exactly-once delivery, or complete MCP/A2A interoperability.
"""

from __future__ import annotations

import enum
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field, field_validator


SCHEMA_VERSION = "1.0.0-exp"


def get_utc_now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


def compute_canonical_payload_hash(payload: Any) -> str:
    """Compute a deterministic SHA-256 hash of a JSON-serializable payload.

    Sorts keys and eliminates extraneous whitespace to guarantee identical
    hashes for semantically identical payloads regardless of key ordering.
    """
    if payload is None:
        payload = {}
    canonical_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


class DialogStatus(str, enum.Enum):
    """Explicit lifecycle states for dialog tasks."""
    RECEIVED = "RECEIVED"
    ACCEPTED = "ACCEPTED"
    RUNNING = "RUNNING"
    RECOVERING = "RECOVERING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

    def is_terminal(self) -> bool:
        return self == DialogStatus.COMPLETED


class DialogMessage(BaseModel):
    """Shared experimental dialog message envelope exchanged across adapters."""

    schema_version: str = Field(
        default=SCHEMA_VERSION,
        description="Version of the experimental correlation schema.",
    )
    dialog_id: str = Field(
        ...,
        description="Globally unique identifier for the logical conversation or interaction.",
    )
    request_id: str = Field(
        ...,
        description="Unique identifier for this specific message transmission or attempt.",
    )
    idempotency_key: str = Field(
        ...,
        description="Stable idempotency key preserved across retries of the same side-effecting task.",
    )
    source_adapter: str = Field(
        ...,
        description="Identifier of the sending agent adapter (e.g., 'adapter_a').",
    )
    target_adapter: str = Field(
        ...,
        description="Identifier of the receiving agent adapter (e.g., 'adapter_b').",
    )
    task_type: str = Field(
        ...,
        description="Discriminator for the requested operation or side effect.",
    )
    payload: Dict[str, Any] = Field(
        default_factory=dict,
        description="Business payload for the operation.",
    )
    status: DialogStatus = Field(
        default=DialogStatus.RECEIVED,
        description="Current lifecycle state of the dialog task.",
    )
    attempt: int = Field(
        default=1,
        ge=1,
        description="Transmission or execution attempt sequence number.",
    )
    result: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Persisted business result once completed.",
    )
    error: Optional[str] = Field(
        default=None,
        description="Error description if status is FAILED.",
    )
    created_at: str = Field(
        default_factory=get_utc_now_iso,
        description="ISO 8601 timestamp of creation.",
    )
    updated_at: str = Field(
        default_factory=get_utc_now_iso,
        description="ISO 8601 timestamp of last state update.",
    )
    version: int = Field(
        default=1,
        ge=1,
        description="Monotonically increasing version number for optimistic locking.",
    )

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, v: str) -> str:
        if v != SCHEMA_VERSION:
            raise ValueError(f"Unsupported schema version: {v}. Expected {SCHEMA_VERSION}")
        return v

    def canonical_payload_hash(self) -> str:
        """Returns the canonical deterministic SHA-256 hash of the payload."""
        return compute_canonical_payload_hash(self.payload)


class DialogStatusResponse(BaseModel):
    """Status response returned by Adapter B to Adapter A."""

    dialog_id: str
    idempotency_key: str
    request_id: str
    status: DialogStatus
    attempt: int
    version: int
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    schema_version: str = SCHEMA_VERSION
    updated_at: str

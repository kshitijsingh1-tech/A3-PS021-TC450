"""Durable SQLite Persistence Layer for PS-021.

Manages schemas, atomic registration via uniqueness constraints,
side-effect ledger, event auditing, and optimistic concurrency.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

from aiori.schema import (
    DialogMessage,
    DialogStatus,
    get_utc_now_iso,
)
from aiori.state_machine import (
    IdempotencyConflictError,
    LifecycleStateMachine,
    OptimisticLockError,
    TerminalStateError,
)

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- Table for tracking high-level dialog sessions
CREATE TABLE IF NOT EXISTS dialogs (
    dialog_id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    current_status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Table for durable operations with idempotency registry
CREATE TABLE IF NOT EXISTS operations (
    idempotency_key TEXT PRIMARY KEY,
    dialog_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    source_adapter TEXT NOT NULL,
    target_adapter TEXT NOT NULL,
    task_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 1,
    result_json TEXT,
    error TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(dialog_id) REFERENCES dialogs(dialog_id)
);

CREATE INDEX IF NOT EXISTS idx_operations_dialog_id ON operations(dialog_id);
CREATE INDEX IF NOT EXISTS idx_operations_status ON operations(status);

-- Table for deterministic mock side effects to verify exact execution count
CREATE TABLE IF NOT EXISTS side_effects (
    effect_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL,
    dialog_id TEXT NOT NULL,
    task_type TEXT NOT NULL,
    action TEXT NOT NULL,
    details_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(idempotency_key) REFERENCES operations(idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_side_effects_idemp ON side_effects(idempotency_key);

-- Table for lifecycle event audit trail
CREATE TABLE IF NOT EXISTS event_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dialog_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_id TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL,
    version INTEGER NOT NULL,
    timestamp TEXT NOT NULL,
    details TEXT
);

CREATE INDEX IF NOT EXISTS idx_event_log_dialog ON event_log(dialog_id);
"""


class DatabaseManager:
    """Encapsulates SQLite connection management and atomic transactional operations."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._initialize_schema()

    def get_connection(self) -> sqlite3.Connection:
        """Create and return a configured SQLite connection."""
        conn = sqlite3.connect(
            self.db_path,
            timeout=15.0,
            isolation_level=None,  # Autocommit mode; we manage explicit transactions
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        return conn

    @contextlib.contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager providing an explicit BEGIN IMMEDIATE transaction."""
        conn = self.get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE;")
            yield conn
            conn.execute("COMMIT;")
        except Exception:
            try:
                conn.execute("ROLLBACK;")
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def _initialize_schema(self) -> None:
        """Initialize database tables and indexes."""
        # Ensure parent directory exists
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self.get_connection() as conn:
            conn.executescript(SCHEMA_SQL)

    def register_or_get_operation(
        self, msg: DialogMessage
    ) -> Tuple[bool, DialogMessage]:
        """Atomically register a new operation or retrieve an existing one.

        Returns:
            (is_new, message): True if newly inserted, False if pre-existing.

        Raises:
            IdempotencyConflictError: If the idempotency_key exists with a different payload.
        """
        payload_hash = msg.canonical_payload_hash()
        payload_json = json.dumps(msg.payload, sort_keys=True)
        now = get_utc_now_iso()

        with self.transaction() as conn:
            # 1. Ensure dialog record exists
            conn.execute(
                """
                INSERT OR IGNORE INTO dialogs (dialog_id, schema_version, current_status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (msg.dialog_id, msg.schema_version, DialogStatus.RECEIVED.value, now, now),
            )

            # 2. Attempt atomic insertion relying on idempotency_key uniqueness constraint
            try:
                conn.execute(
                    """
                    INSERT INTO operations (
                        idempotency_key, dialog_id, request_id, source_adapter, target_adapter,
                        task_type, payload_json, payload_hash, status, attempt, result_json,
                        error, version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        msg.idempotency_key,
                        msg.dialog_id,
                        msg.request_id,
                        msg.source_adapter,
                        msg.target_adapter,
                        msg.task_type,
                        payload_json,
                        payload_hash,
                        DialogStatus.RECEIVED.value,
                        msg.attempt,
                        None,
                        None,
                        1,
                        now,
                        now,
                    ),
                )

                # Record event in audit log
                conn.execute(
                    """
                    INSERT INTO event_log (dialog_id, idempotency_key, request_id, from_status, to_status, version, timestamp, details)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        msg.dialog_id,
                        msg.idempotency_key,
                        msg.request_id,
                        None,
                        DialogStatus.RECEIVED.value,
                        1,
                        now,
                        "Initial atomic registration",
                    ),
                )

                # Return fresh message reflecting database state
                new_msg = msg.model_copy(
                    update={
                        "status": DialogStatus.RECEIVED,
                        "version": 1,
                        "created_at": now,
                        "updated_at": now,
                    }
                )
                return True, new_msg

            except sqlite3.IntegrityError as e:
                # Key already exists: fetch existing record and verify payload
                row = conn.execute(
                    "SELECT * FROM operations WHERE idempotency_key = ?",
                    (msg.idempotency_key,),
                ).fetchone()

                if not row:
                    raise e  # If foreign key or other integrity error

                existing_hash = row["payload_hash"]
                if existing_hash != payload_hash:
                    raise IdempotencyConflictError(
                        f"Idempotency key '{msg.idempotency_key}' was previously used with "
                        f"a different payload (hash: {existing_hash[:8]} vs {payload_hash[:8]})."
                    )

                existing_msg = self._row_to_dialog_message(row)
                return False, existing_msg

    def update_operation_status(
        self,
        conn: sqlite3.Connection,
        idempotency_key: str,
        expected_version: int,
        target_status: DialogStatus,
        request_id: Optional[str] = None,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
        attempt: Optional[int] = None,
        audit_details: Optional[str] = None,
    ) -> DialogMessage:
        """Durably update operation lifecycle status using optimistic concurrency control.

        Must be executed within an existing transaction `conn`.
        """
        row = conn.execute(
            "SELECT * FROM operations WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()

        if not row:
            raise KeyError(f"Operation with idempotency_key {idempotency_key} not found.")

        current_status = DialogStatus(row["status"])
        current_version = row["version"]

        if current_version != expected_version:
            raise OptimisticLockError(
                f"Version conflict for {idempotency_key}: expected {expected_version}, found {current_version}."
            )

        # Enforce lifecycle transition rules
        LifecycleStateMachine.validate_transition(current_status, target_status)

        new_version = current_version + 1
        now = get_utc_now_iso()
        result_json = json.dumps(result) if result is not None else row["result_json"]
        error_val = error if error is not None else row["error"]
        new_attempt = attempt if attempt is not None else row["attempt"]
        req_id = request_id if request_id is not None else row["request_id"]

        cursor = conn.execute(
            """
            UPDATE operations
            SET status = ?,
                version = ?,
                result_json = ?,
                error = ?,
                attempt = ?,
                request_id = ?,
                updated_at = ?
            WHERE idempotency_key = ? AND version = ?
            """,
            (
                target_status.value,
                new_version,
                result_json,
                error_val,
                new_attempt,
                req_id,
                now,
                idempotency_key,
                current_version,
            ),
        )

        if cursor.rowcount != 1:
            raise OptimisticLockError(
                f"Optimistic lock race updating {idempotency_key} from version {current_version}."
            )

        # Also update parent dialog current_status and updated_at
        conn.execute(
            "UPDATE dialogs SET current_status = ?, updated_at = ? WHERE dialog_id = ?",
            (target_status.value, now, row["dialog_id"]),
        )

        # Append audit log
        conn.execute(
            """
            INSERT INTO event_log (dialog_id, idempotency_key, request_id, from_status, to_status, version, timestamp, details)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["dialog_id"],
                idempotency_key,
                req_id,
                current_status.value,
                target_status.value,
                new_version,
                now,
                audit_details or f"Transitioned from {current_status.value} to {target_status.value}",
            ),
        )

        # Fetch and return updated record
        updated_row = conn.execute(
            "SELECT * FROM operations WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        return self._row_to_dialog_message(updated_row)

    def record_side_effect(
        self,
        conn: sqlite3.Connection,
        effect_id: str,
        idempotency_key: str,
        dialog_id: str,
        task_type: str,
        action: str,
        details: Dict[str, Any],
    ) -> None:
        """Insert a durable side-effect entry within the active SQLite transaction."""
        now = get_utc_now_iso()
        conn.execute(
            """
            INSERT INTO side_effects (effect_id, idempotency_key, dialog_id, task_type, action, details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                effect_id,
                idempotency_key,
                dialog_id,
                task_type,
                action,
                json.dumps(details),
                now,
            ),
        )

    def count_side_effects(self, idempotency_key: str) -> int:
        """Count the number of side effects committed for a given idempotency key."""
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM side_effects WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            return int(row["cnt"]) if row else 0

    def get_side_effects(self, idempotency_key: str) -> List[Dict[str, Any]]:
        """Retrieve all committed side effects for a given idempotency key."""
        with self.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM side_effects WHERE idempotency_key = ? ORDER BY created_at ASC",
                (idempotency_key,),
            ).fetchall()
            return [
                {
                    "effect_id": r["effect_id"],
                    "idempotency_key": r["idempotency_key"],
                    "dialog_id": r["dialog_id"],
                    "task_type": r["task_type"],
                    "action": r["action"],
                    "details": json.loads(r["details_json"]) if r["details_json"] else {},
                    "created_at": r["created_at"],
                }
                for r in rows
            ]

    def get_operation(self, idempotency_key: str) -> Optional[DialogMessage]:
        """Retrieve an operation by idempotency_key."""
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM operations WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if not row:
                return None
            return self._row_to_dialog_message(row)

    def get_operations_by_dialog(self, dialog_id: str) -> List[DialogMessage]:
        """Retrieve all operations associated with a dialog_id."""
        with self.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM operations WHERE dialog_id = ? ORDER BY created_at ASC",
                (dialog_id,),
            ).fetchall()
            return [self._row_to_dialog_message(r) for r in rows]

    def get_non_terminal_operations(self) -> List[DialogMessage]:
        """Fetch all operations that are not in terminal state COMPLETED."""
        with self.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM operations
                WHERE status IN ('RECEIVED', 'ACCEPTED', 'RUNNING', 'RECOVERING')
                ORDER BY created_at ASC
                """
            ).fetchall()
            return [self._row_to_dialog_message(r) for r in rows]

    def get_all_operations(self) -> List[DialogMessage]:
        """Retrieve all operations."""
        with self.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM operations ORDER BY created_at ASC"
            ).fetchall()
            return [self._row_to_dialog_message(r) for r in rows]

    def get_audit_events(self, dialog_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retrieve audit event records."""
        with self.get_connection() as conn:
            if dialog_id:
                rows = conn.execute(
                    "SELECT * FROM event_log WHERE dialog_id = ? ORDER BY id ASC",
                    (dialog_id,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM event_log ORDER BY id ASC").fetchall()

            return [
                {
                    "id": r["id"],
                    "dialog_id": r["dialog_id"],
                    "idempotency_key": r["idempotency_key"],
                    "request_id": r["request_id"],
                    "from_status": r["from_status"],
                    "to_status": r["to_status"],
                    "version": r["version"],
                    "timestamp": r["timestamp"],
                    "details": r["details"],
                }
                for r in rows
            ]

    def _row_to_dialog_message(self, row: sqlite3.Row) -> DialogMessage:
        return DialogMessage(
            schema_version=row["schema_version"] if "schema_version" in row.keys() else "1.0.0-exp",
            dialog_id=row["dialog_id"],
            request_id=row["request_id"],
            idempotency_key=row["idempotency_key"],
            source_adapter=row["source_adapter"],
            target_adapter=row["target_adapter"],
            task_type=row["task_type"],
            payload=json.loads(row["payload_json"]) if row["payload_json"] else {},
            status=DialogStatus(row["status"]),
            attempt=row["attempt"],
            result=json.loads(row["result_json"]) if row["result_json"] else None,
            error=row["error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            version=row["version"],
        )

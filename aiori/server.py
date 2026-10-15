"""Adapter B: Server-side Mock Agent Adapter (FastAPI) for PS-021.

Hosts the durable idempotency registry, executes tasks with transactional
side-effects, triggers deterministic fault injection, and runs the recovery
manager on startup.
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Header, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse

from aiori.config import DEFAULT_DB_PATH
from aiori.db import DatabaseManager
from aiori.idempotency import IdempotencyEngine
from aiori.recovery import RecoveryReport, StartupRecoveryManager
from aiori.schema import (
    DialogMessage,
    DialogStatus,
    DialogStatusResponse,
)
from aiori.state_machine import IdempotencyConflictError, TerminalStateError

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("aiori.adapter_b")

# Global instances initialized during app creation / lifespan
db_manager: Optional[DatabaseManager] = None
idempotency_engine: Optional[IdempotencyEngine] = None
latest_recovery_report: Optional[RecoveryReport] = None


def get_db() -> DatabaseManager:
    global db_manager
    if db_manager is None:
        db_path = os.environ.get("AIORI_DB_PATH", DEFAULT_DB_PATH)
        db_manager = DatabaseManager(db_path)
    return db_manager


def get_engine() -> IdempotencyEngine:
    global idempotency_engine
    if idempotency_engine is None:
        idempotency_engine = IdempotencyEngine(get_db())
    return idempotency_engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager that executes Startup Recovery on boot."""
    global latest_recovery_report
    db = get_db()
    logger.info("Adapter B booting up. Database path: %s", db.db_path)

    # Invoke Recovery Manager
    recovery_mgr = StartupRecoveryManager(db)
    latest_recovery_report = recovery_mgr.run_recovery()
    logger.info(
        "Startup Recovery complete. Found: %d, Reconciled: %d, Resumed: %d",
        latest_recovery_report.total_found,
        latest_recovery_report.reconciled_existing_effects,
        latest_recovery_report.resumed_and_completed,
    )
    yield
    logger.info("Adapter B shutting down.")


def create_app(custom_db_path: Optional[str] = None) -> FastAPI:
    """Application factory for testing and standalone execution."""
    global db_manager, idempotency_engine
    if custom_db_path:
        os.environ["AIORI_DB_PATH"] = custom_db_path
        db_manager = DatabaseManager(custom_db_path)
        idempotency_engine = IdempotencyEngine(db_manager)

    app = FastAPI(
        title="PS-021 Adapter B (Server-side Mock Agent Adapter)",
        version="1.0.0-exp",
        lifespan=lifespan,
    )

    @app.exception_handler(IdempotencyConflictError)
    async def idempotency_conflict_handler(request, exc: IdempotencyConflictError):
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": str(exc), "error_type": "IdempotencyConflictError"},
        )

    @app.exception_handler(TerminalStateError)
    async def terminal_state_handler(request, exc: TerminalStateError):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": str(exc), "error_type": "TerminalStateError"},
        )

    @app.get("/health")
    def health():
        return {"status": "HEALTHY", "adapter": "adapter_b"}

    @app.get("/recovery/status")
    def recovery_status():
        """Retrieve results of the most recent startup recovery run."""
        global latest_recovery_report
        if latest_recovery_report is None:
            return {"status": "NO_RUN_YET"}
        return latest_recovery_report.to_dict()

    @app.post("/dialog/task", response_model=DialogStatusResponse)
    def submit_task(
        msg: DialogMessage,
        x_fault_action: Optional[str] = Header(None, alias="X-Fault-Action"),
        fault_action: Optional[str] = Query(None),
        response: Response = None,
    ):
        """Submit a task to Adapter B.

        Executes atomic registration, idempotency verification, side-effect
        execution, and lifecycle transitions. Supports fault injection directives.
        """
        engine = get_engine()
        directive = x_fault_action or fault_action

        try:
            op, status_code = engine.process_message(msg, fault_action=directive)
            if response:
                response.status_code = status_code
            return DialogStatusResponse(
                dialog_id=op.dialog_id,
                idempotency_key=op.idempotency_key,
                request_id=op.request_id,
                status=op.status,
                attempt=op.attempt,
                version=op.version,
                result=op.result,
                error=op.error,
                updated_at=op.updated_at,
            )
        except ConnectionResetError as e:
            # Simulated dropped connection
            logger.warning("Simulated connection drop triggered.")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(e),
            )

    @app.get("/dialog/{dialog_id}/status")
    def get_dialog_status(dialog_id: str):
        """Retrieve status and operations for a given dialog."""
        db = get_db()
        operations = db.get_operations_by_dialog(dialog_id)
        if not operations:
            raise HTTPException(status_code=404, detail=f"Dialog '{dialog_id}' not found.")
        return {
            "dialog_id": dialog_id,
            "operations_count": len(operations),
            "operations": [op.model_dump() for op in operations],
        }

    @app.get("/dialog/operation/{idempotency_key}", response_model=DialogStatusResponse)
    def get_operation(idempotency_key: str):
        """Retrieve detailed status and stored result for an operation."""
        db = get_db()
        op = db.get_operation(idempotency_key)
        if not op:
            raise HTTPException(
                status_code=404,
                detail=f"Operation with idempotency key '{idempotency_key}' not found.",
            )
        return DialogStatusResponse(
            dialog_id=op.dialog_id,
            idempotency_key=op.idempotency_key,
            request_id=op.request_id,
            status=op.status,
            attempt=op.attempt,
            version=op.version,
            result=op.result,
            error=op.error,
            updated_at=op.updated_at,
        )

    @app.get("/dialog/operation/{idempotency_key}/side-effects")
    def get_side_effects(idempotency_key: str):
        """Inspect durable side-effects ledger for test assertions."""
        db = get_db()
        effects = db.get_side_effects(idempotency_key)
        return {
            "idempotency_key": idempotency_key,
            "side_effect_count": len(effects),
            "side_effects": effects,
        }

    @app.get("/dialog/audit-log")
    def get_audit_log(dialog_id: Optional[str] = Query(None)):
        """Retrieve lifecycle transition audit log."""
        db = get_db()
        return {"events": db.get_audit_events(dialog_id)}

    @app.post("/fault/kill")
    def kill_process():
        """Fault injection endpoint: abruptly terminate the process."""
        logger.warning("FAULT INJECTION: Force kill requested via /fault/kill.")
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(99)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    from aiori.config import DEFAULT_SERVER_HOST, DEFAULT_SERVER_PORT

    uvicorn.run(app, host=DEFAULT_SERVER_HOST, port=DEFAULT_SERVER_PORT)

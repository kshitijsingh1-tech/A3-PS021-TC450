# Source Code Directory

This directory contains the primary source code implementation for **PS-021: Experimental Dialog Correlation and Recovery Across Agent Adapters** (Team: **Cache Us If You Can**, Code: **A3-PS021-TC450**).

## Package Architecture & Modules

The `aiori` package is organized into modular components:

- [`aiori/schema.py`](aiori/schema.py): Experimental `DialogMessage` envelope schema, metadata validation, and canonical SHA-256 payload hashing (`compute_payload_hash`).
- [`aiori/state_machine.py`](aiori/state_machine.py): Explicit 6-state lifecycle state machine (`RECEIVED`, `ACCEPTED`, `RUNNING`, `COMPLETED`, `FAILED`, `RECOVERING`) and valid transition rules.
- [`aiori/db.py`](aiori/db.py): SQLite WAL database manager, atomic task registration with `BEGIN IMMEDIATE` transaction serialization, and idempotent state storage.
- [`aiori/side_effects.py`](aiori/side_effects.py): Deterministic domain side-effect engine with atomic execution ledger and duplicate execution checks.
- [`aiori/idempotency.py`](aiori/idempotency.py): Idempotency registry, deduplication engine, and deterministic crash fault-injection triggers.
- [`aiori/recovery.py`](aiori/recovery.py): Automated startup crash recovery manager (`StartupRecoveryManager`) that reconciles or resumes interrupted work.
- [`aiori/server.py`](aiori/server.py): Server-side Adapter B FastAPI application with lifespan recovery, task execution, and status query endpoints.
- [`aiori/client.py`](aiori/client.py): Client-side Adapter A HTTP client with exponential backoff, jitter, retry policies, and attempt sequence tracking.
- [`aiori/config.py`](aiori/config.py): Environment configuration defaults and database path settings.
- [`aiori/demo.py`](aiori/demo.py): End-to-end scenario runner and report generator.

## Running from Source

You can run the full automated demo from root:
```powershell
python demo.py
```

Or execute the complete pytest verification suite:
```powershell
python -m pytest -v
```

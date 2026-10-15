# PS-021: Architecture & System Design

> **Notice:** This document details an **experimental prototype** for dialog correlation and recovery across agent adapters. It does not claim official IETF standard compliance or complete MCP/A2A interoperability. It does not claim universal exactly-once delivery across uncoordinated network boundaries.

---

## 1. System Overview

PS-021 demonstrates reliable dialog correlation, durable state persistence, request deduplication, and crash recovery between two mock agent adapters:

- **Adapter A (Client-side Mock Adapter):** Initiates task requests, manages attempt numbering, stable dialog and idempotency identifiers, and executes automated retry policies upon network or server failures.
- **Adapter B (Server-side Mock Adapter / FastAPI):** Receives dialog requests, atomically registers them within a durable SQLite database, enforces lifecycle transitions, executes business logic side effects within the same SQLite transaction, suppresses duplicates, and runs a crash recovery protocol during startup.

```mermaid
flowchart TD
    subgraph AdapterA ["Adapter A (Client Mock)"]
        A1[Caller / Agent Task] --> A2[Envelope Formatter<br/>dialog_id, stable idemp_key, fresh request_id]
        A2 --> A3[HTTP Client / Retry Manager]
    end

    subgraph Transport ["HTTP Protocol (JSON)"]
        A3 -- "POST /dialog/task (Attempt 1..N)" --> B1
        B5 -- "HTTP 200 (Result) / 202 (In-Progress)" --> A3
    end

    subgraph AdapterB ["Adapter B (FastAPI Server)"]
        B1[FastAPI Route Handler] --> B2[Idempotency Engine]
        B2 --> B3{Atomic Unique Check}
        B3 -- "New Request" --> B4[State Machine: RECEIVED -> ACCEPTED -> RUNNING]
        B4 --> B5[Atomic Tx: Execute Side Effect + COMPLETED]
        B3 -- "Duplicate COMPLETED" --> B6[Return Stored Result]
        B3 -- "Duplicate RUNNING" --> B7[Return 202 In-Progress]
        B3 -- "Payload Mismatch" --> B8[Raise HTTP 409 Conflict]
        
        Startup[Server Boot Lifespan] --> RecMgr[Startup Recovery Manager]
        RecMgr --> DB_Inspect[(SQLite State Inspection)]
        RecMgr --> Reconcile[Reconcile Committed Effects / Resume Safe Work]
    end

    subgraph Storage ["SQLite WAL Storage"]
        DB1[(dialogs)]
        DB2[(operations)]
        DB3[(side_effects ledger)]
        DB4[(event_log)]
    end

    B4 -.-> DB2
    B5 -.-> DB2
    B5 -.-> DB3
    B5 -.-> DB4
    DB_Inspect -.-> DB2
    DB_Inspect -.-> DB3
```

---

## 2. Core Architectural Principles

### 2.1 Separation of Identifiers
- `dialog_id`: Stable throughout the lifetime of the logical conversation or cross-agent workflow.
- `idempotency_key`: Stable identifier representing a single intended business side effect across multiple retry attempts.
- `request_id`: Fresh, unique identifier generated per transmission attempt (e.g., `req_abc123` on attempt 1, `req_def456` on attempt 2).
- `attempt`: Monotonically increasing sequence integer (1, 2, ...).

### 2.2 Atomic Registration via Uniqueness Constraints
Registration of incoming requests never uses a non-transactional check-then-insert pattern. Instead, `idempotency_key` is backed by a primary key uniqueness constraint in SQLite. Concurrency is serialized using SQLite `BEGIN IMMEDIATE` transactions, eliminating duplicate creation races.

### 2.3 Single-Transaction Atomicity for Local Side Effects
For side effects internal to the SQLite database (e.g., account balance changes, reservation bookings, ledger entries), both the business mutation in `side_effects` and the state transition to `COMPLETED` occur in the **exact same SQLite transaction**.
- If a crash occurs before commit: both the side effect and the completion status roll back entirely.
- If a crash occurs after commit: both the side effect and the `COMPLETED` status persist durably on disk.

### 2.4 External Side Effects Disclaimer
In distributed architectures where an external service (e.g., Stripe, third-party LLM, external API) is invoked, atomic transactions across heterogeneous systems are impossible without two-phase commit (2PC) or compensating workflows. For such systems, the adapter pattern relies on external idempotency keys, outbox tables, and explicit reconciliation queries.

### 2.5 Startup Recovery Protocol
Upon process reboot, Adapter B executes `StartupRecoveryManager` during its FastAPI lifespan before accepting inbound traffic:
1. Queries all non-terminal operations (`RECEIVED`, `ACCEPTED`, `RUNNING`, `RECOVERING`).
2. Checks the durable `side_effects` ledger for each operation.
3. If the side effect was committed: reconciles the operation directly to `COMPLETED` using stored details without duplicate execution.
4. If the side effect was not committed: safely advances through `RECOVERING -> RUNNING`, executes the deterministic side effect, and transitions to `COMPLETED`.
5. Preserves all original `dialog_id` and `idempotency_key` bindings.

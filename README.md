# AIORI-3: PS-021 — Experimental Dialog Correlation and Recovery Across Agent Adapters

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111+-green.svg)](https://fastapi.tiangolo.com)
[![Pydantic v2](https://img.shields.io/badge/Pydantic-2.x-orange.svg)](https://docs.pydantic.dev/)
[![SQLite WAL](https://img.shields.io/badge/SQLite-WAL-lightgrey.svg)](https://www.sqlite.org/wal.html)
[![Tests Passing](https://img.shields.io/badge/pytest-13%20passed-brightgreen.svg)]()
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

---

## Team Information

| Attribute | Details |
| :--- | :--- |
| **Hackathon** | AIORI Hackathon 3 (AIORI-3) |
| **Problem Statement** | **PS-021**: Experimental Dialog Correlation and Recovery Across Agent Adapters |
| **Team Name** | **Cache Us If You Can** |
| **Team Code** | `A3-PS021-TC450` |
| **Mentor** | **Prof. Rajni Devi** |
| **Team Members** | **Kshitij Singh**, **Kiranpreet Kaur** |

---

## Deliverables & Quick Links

- **Team Introduction & Live Demo Video:** [https://youtu.be/7fRWo1-o7_k](https://youtu.be/7fRWo1-o7_k)  
  *Covers team introduction, problem statement breakdown, architectural design, and live execution of automated fault-injection and recovery scenarios.*
- **Presentation (PDF):** [`PS-021-Experimental-Dialog-Correlation-and-Recovery-Across-Agent-Adapters.pdf`](PS-021-Experimental-Dialog-Correlation-and-Recovery-Across-Agent-Adapters.pdf)  
  *Structured strictly according to the official hackathon template structure ([`Proposed-structure-hackathon.pdf`](Proposed-structure-hackathon.pdf)).*
- **Interactive Showcase Webpage:** [`docs/demo-site/index.html`](docs/demo-site/index.html)  
  *Interactive visual dashboard simulating real fault injection and recovery scenarios across Adapter A and Adapter B, animated 6-state lifecycle transitions, and live retry traces.*
- **Working Demo Script:** Run `python demo.py` for full automated scenario reproduction.
- **Automated Test Suite:** 13 verified pytest test cases with live process crash injection (`python -m pytest -v`).
- **Comprehensive Technical Documentation:**
  - [Architecture & Sequence Diagrams](docs/architecture.md)
  - [Schema, Canonical Hashing & State Matrix](docs/schema.md)
  - [Algorithmic Pseudocode](docs/pseudocode.md)
  - [Test Results & Verification Matrix](docs/test-results.md)

---

## Executive Summary & Scope

In multi-agent architectures and agent adapters, **a missing response is not proof of a missing execution**. Network disconnects, transport timeouts, or sudden server process terminations leave clients uncertain whether their requests were received, executed, or dropped. Uncoordinated retries risk duplicate execution of critical side effects (such as double payments or duplicate external tool invocations).

This prototype delivers a lightweight, robust reference architecture solving this challenge using:
1. **Three-Identity Model:**
   - `dialog_id`: Stable correlation identifier for the long-lived conversation or workflow.
   - `idempotency_key`: Stable client-chosen identifier for one intended business operation.
   - `request_id`: Fresh identifier generated per network transmission attempt.
   - `attempt`: Monotonically increasing attempt sequence counter.
2. **Explicit 6-State Lifecycle Machine:**
   `RECEIVED` &rarr; `ACCEPTED` &rarr; `RUNNING` &rarr; `COMPLETED` / `FAILED` / `RECOVERING`.
3. **Atomic Registration & Dedup Engine:**
   Uses SQLite in WAL mode with `BEGIN IMMEDIATE` transaction serialization and uniqueness constraints to guarantee that duplicate and concurrent submissions are safely serialized and deduplicated without race conditions.
4. **Startup Crash Recovery Manager:**
   Upon server restart, Adapter B automatically inspects interrupted operations and checks durable proof in the domain side-effects ledger, either completing or reconciling in-flight operations while preserving conversation identity.

> **Disclaimer:** This project implements an **experimental** schema and reference architecture. It does not claim official IETF standard compliance or complete MCP/A2A interoperability. It does not claim universal exactly-once delivery across uncoordinated external services.

---

## Directory Structure

```text
A3-PS021-TC450/
├── PS-021-Experimental-Dialog-Correlation-and-Recovery-Across-Agent-Adapters.pdf # Official presentation
├── Proposed-structure-hackathon.pdf # Hackathon template structure reference
├── demo.py                        # Single-command demonstration runner
├── pytest.ini                     # Pytest configuration
├── requirements.txt               # Dependencies
├── aiori/                         # Core Python implementation package
│   ├── schema.py                  # Experimental DialogMessage schema & canonical hashing
│   ├── state_machine.py           # Explicit 6-state lifecycle state machine
│   ├── db.py                      # SQLite WAL database manager & atomic registration
│   ├── side_effects.py            # Deterministic domain side-effect engine & audit ledger
│   ├── idempotency.py             # Idempotency registry & fault injection triggers
│   ├── recovery.py                # Automated startup crash recovery manager
│   ├── server.py                  # Adapter B FastAPI application & endpoints
│   ├── client.py                  # Adapter A HTTP client with retry policies
│   ├── config.py                  # Environment configuration defaults
│   └── demo.py                    # Demonstration scenario runner & report generator
├── src/                           # Source directory (hackathon template compliant)
│   ├── README.md                  # Source code index
│   └── aiori/                     # Core package files
├── tests/                         # Pytest test suite (13 passing tests)
│   ├── conftest.py                # Subprocess management & test fixtures
│   ├── test_schema_and_state.py   # Schema validation & state transition tests
│   ├── test_scenario_1_lost_request.py       # Scenario 1: Request lost before delivery
│   ├── test_scenario_2_crash_during_exec.py  # Scenario 2: Mid-execution crash & recovery
│   ├── test_scenario_3_lost_response.py      # Scenario 3: Lost response post-commit
│   ├── test_scenario_4_concurrent_duplicates.py # Scenario 4: Concurrent duplicate handling
│   ├── test_scenario_5_mixed_recovery.py     # Scenario 5: Mixed completed & pending recovery
│   └── test_scenario_6_idempotency_conflict.py # Scenario 6: 409 conflict payload mismatch
└── docs/                          # Detailed engineering documentation
    ├── demo-site/                 # Interactive visual simulation webpage
    │   ├── index.html             # Interactive showcase application
    │   ├── style.css              # Dark theme with warm earthy accents
    │   └── script.js              # Scenario simulation engine & trace visualizer
    ├── architecture.md            # Architectural overview & Mermaid sequence flows
    ├── schema.md                  # Envelope fields, payload hashing & state matrix
    ├── pseudocode.md              # Algorithmic pseudocode for core mechanisms
    └── test-results.md            # Test matrix execution logs & verification proofs
```

---

## Quickstart & Execution Guide

### 1. Requirements

- Python 3.11 or higher
- Windows PowerShell, macOS, or Linux terminal
- Dependencies: `fastapi`, `uvicorn`, `pydantic`, `httpx`, `pytest`

### 2. Installation

Clone the repository and install dependencies:

```bash
git clone https://github.com/kshitijsingh1-tech/A3-PS021-TC450.git
cd A3-PS021-TC450
pip install -r requirements.txt
```

### 3. Run the Automated End-to-End Demonstration

Execute the demonstration command:

```powershell
python demo.py
```

This runs all 6 core fault-injection and recovery scenarios end-to-end, printing a verified execution report:

```text
Executing PS-021 Automated Fault-Injection & Recovery Scenarios...

============================================================================================
       PS-021: DIALOG CORRELATION & STATE RECOVERY DEMONSTRATION REPORT       
============================================================================================
Scenario Name                          | Dialog ID      | Status          | Preserved | Effects | Result
--------------------------------------------------------------------------------------------
1. Lost Request Before Delivery        | dlg_s1_...     | COMPLETED       | YES       | 1       | PASS  
2. Crash Mid-Execution & Startup Recovery | dlg_s2_...  | COMPLETED       | YES       | 1       | PASS  
3. Response Lost Post-Commit & Deduplication | dlg_s3_...| COMPLETED       | YES       | 1       | PASS  
4. Concurrent Duplicate Serialization  | dlg_s4_...     | COMPLETED       | YES       | 1       | PASS  
5. Mixed Completed & Pending Recovery  | dlg_s5_...     | COMPLETED (4/4) | YES       | 4       | PASS  
6. Idempotency Key Payload Conflict    | dlg_s6_...     | 409 CONFLICT    | YES       | 1       | PASS  
--------------------------------------------------------------------------------------------
Summary:
Total Scenarios Executed: 6
Passed: 6 / 6
System Recovery Verification: ALL TESTS PASSED
```

### 4. Run the Full Test Suite

Run all 13 test scenarios verifying edge cases, crash injection, and state machine invariants:

```powershell
python -m pytest -v
```

All 13 tests execute live subprocess spawns and SIGKILL / SIGTERM crashes to verify recovery from persistent SQLite storage.

---

## Interactive Manual Testing (2-Process Setup)

If you wish to run Adapter B as a standalone service and interact with Adapter A manually:

### Terminal 1: Adapter B (FastAPI Server)
```powershell
$env:AIORI_DB_PATH = "my_state.db"
python -m uvicorn aiori.server:app --host 127.0.0.1 --port 8000 --reload
```

### Terminal 2: Adapter A (Client Interaction)
```powershell
python -c "from aiori.client import AdapterAClient; c = AdapterAClient('http://127.0.0.1:8000'); resp = c.send_with_retry('dlg_test_01', 'idemp_key_999', 'payment_transfer', {'amount': 500.0, 'account': 'ACC-1234'}); print(resp)"
```

You can inspect the durable SQLite database state at any time:
```powershell
python -c "import sqlite3; conn = sqlite3.connect('my_state.db'); print(conn.execute('SELECT idempotency_key, status, attempt, result_payload FROM operations').fetchall())"
```

---

## Core Invariants Verified

| Invariant | Mechanism | Status |
| :--- | :--- | :---: |
| **No Duplicate Side Effects** | Domain action ledger checks executed records in single transaction before commit | **VERIFIED** |
| **Idempotent Retry Safety** | Completed operations return cached response; running operations return 202 In Progress | **VERIFIED** |
| **Crash Durability** | Uncommitted work rolls back cleanly; committed work survives SIGKILL process termination | **VERIFIED** |
| **Identity Preservation** | `dialog_id` and `idempotency_key` are preserved across retries and crashes | **VERIFIED** |
| **Payload Integrity** | Reusing an idempotency key with different payload triggers HTTP 409 Conflict | **VERIFIED** |
| **Concurrent Serialization** | `BEGIN IMMEDIATE` prevents race conditions during simultaneous identical submissions | **VERIFIED** |

---

## Hackathon Submission Checklist

- [x] **Repository Named as Team Code:** `A3-PS021-TC450`
- [x] **Collaborator Added:** `aiori-hackathon`
- [x] **Presentation Uploaded:** `PS-021-Experimental-Dialog-Correlation-and-Recovery-Across-Agent-Adapters.pdf` following `Proposed-structure-hackathon.pdf`
- [x] **Team Introduction & Solution Video:** [https://youtu.be/7fRWo1-o7_k](https://youtu.be/7fRWo1-o7_k)
- [x] **Source Code & Documentation:** Complete `aiori` package, `src/` directory, `tests/`, and `docs/`
- [x] **Working Demo Included:** Verified `python demo.py` runner and 13-test automated verification suite

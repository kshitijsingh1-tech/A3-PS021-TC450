# PS-021: Pseudocode Reference

This document presents algorithmic pseudocode for the three pivotal distributed mechanisms implemented in PS-021:

1. Atomic Request Registration & Idempotency Check
2. Transactional Execution & Deduplication
3. Startup Crash Recovery & Reconciliation

---

## 1. Atomic Request Registration & Idempotency Check

```python
FUNCTION register_or_get_operation(message: DialogMessage) -> (is_new: Boolean, record: DialogMessage):
    canonical_hash = compute_sha256(canonical_json(message.payload))

    BEGIN IMMEDIATE TRANSACTION:
        # Step 1: Ensure dialog container exists
        INSERT OR IGNORE INTO dialogs(dialog_id, schema_version, current_status, created_at, updated_at)
        VALUES (message.dialog_id, message.schema_version, 'RECEIVED', now(), now())

        # Step 2: Attempt atomic insertion relying on PRIMARY KEY constraint
        TRY:
            INSERT INTO operations(
                idempotency_key, dialog_id, request_id, source_adapter, target_adapter,
                task_type, payload_json, payload_hash, status, attempt, version, created_at, updated_at
            ) VALUES (
                message.idempotency_key, message.dialog_id, message.request_id,
                message.source_adapter, message.target_adapter, message.task_type,
                json(message.payload), canonical_hash, 'RECEIVED', message.attempt, 1, now(), now()
            )
            COMMIT TRANSACTION
            RETURN (True, message with status='RECEIVED', version=1)

        EXCEPT IntegrityError:
            # Step 3: Handle duplicate idempotency key
            existing = SELECT * FROM operations WHERE idempotency_key = message.idempotency_key
            
            # Step 4: Semantic conflict validation
            IF existing.payload_hash != canonical_hash:
                ROLLBACK TRANSACTION
                RAISE IdempotencyConflictError("Payload mismatch for existing idempotency key")
            
            COMMIT TRANSACTION
            RETURN (False, existing)
```

---

## 2. Transactional Execution & Deduplication

```python
FUNCTION process_message(message: DialogMessage) -> (response: DialogMessage, http_code: Integer):
    is_new, op = register_or_get_operation(message)

    IF NOT is_new:
        # Duplicate handling
        IF op.status == COMPLETED:
            # Duplicate completed request: return stored result without re-executing
            RETURN (op, 200_OK)
        
        IF op.status IN [RECEIVED, ACCEPTED, RUNNING, RECOVERING]:
            # Duplicate in-progress request: return ongoing state
            RETURN (op, 202_ACCEPTED)
        
        IF op.status == FAILED:
            IF message.attempt > op.attempt:
                # Explicit permitted retry: transition back to ACCEPTED
                op = update_status(op.key, expected_version=op.version, new_status=ACCEPTED, attempt=message.attempt)
            ELSE:
                RETURN (op, 200_OK)

    # State transition: advance newly registered request to ACCEPTED
    IF op.status == RECEIVED:
        op = update_status(op.key, expected_version=op.version, new_status=ACCEPTED)

    # State transition: advance ACCEPTED to RUNNING
    IF op.status == ACCEPTED:
        op = update_status(op.key, expected_version=op.version, new_status=RUNNING)

    # Single-transaction side effect commit
    BEGIN IMMEDIATE TRANSACTION:
        result = execute_business_side_effect(op)
        INSERT INTO side_effects(effect_id, idempotency_key, dialog_id, task_type, action, details)
        VALUES (generate_uuid(), op.idempotency_key, op.dialog_id, op.task_type, result.action, result.details)

        op = UPDATE operations
             SET status = 'COMPLETED', result_json = json(result), version = version + 1
             WHERE idempotency_key = op.idempotency_key AND version = op.version
    COMMIT TRANSACTION

    RETURN (op, 200_OK)
```

---

## 3. Startup Crash Recovery & Reconciliation Protocol

```python
FUNCTION run_startup_recovery():
    # Invoked automatically on server boot lifespan
    non_terminal_tasks = SELECT * FROM operations 
                         WHERE status IN ('RECEIVED', 'ACCEPTED', 'RUNNING', 'RECOVERING')

    FOR task IN non_terminal_tasks:
        effects = SELECT * FROM side_effects WHERE idempotency_key = task.idempotency_key

        BEGIN IMMEDIATE TRANSACTION:
            current = SELECT * FROM operations WHERE idempotency_key = task.idempotency_key
            
            IF current.status == COMPLETED:
                CONTINUE  # Already completed concurrently

            IF length(effects) > 0:
                # Case 1: Side effect ALREADY committed in DB before crash
                # Transition through RECOVERING to COMPLETED without re-executing side effect
                UPDATE operations 
                SET status = 'RECOVERING', version = version + 1 
                WHERE idempotency_key = task.idempotency_key

                stored_result = reconstruct_result_from_side_effect(effects[0])
                UPDATE operations 
                SET status = 'COMPLETED', result_json = json(stored_result), version = version + 1 
                WHERE idempotency_key = task.idempotency_key

                LOG "Reconciled task without duplicate side effect"

            ELSE:
                # Case 2: Side effect was NOT committed prior to crash
                # Interrupted mid-flight; safe to resume
                UPDATE operations 
                SET status = 'RECOVERING', version = version + 1 
                WHERE idempotency_key = task.idempotency_key

                UPDATE operations 
                SET status = 'RUNNING', version = version + 1 
                WHERE idempotency_key = task.idempotency_key

                result = execute_business_side_effect(task)
                INSERT INTO side_effects VALUES (generate_uuid(), task.idempotency_key, task.dialog_id, ...)

                UPDATE operations 
                SET status = 'COMPLETED', result_json = json(result), version = version + 1 
                WHERE idempotency_key = task.idempotency_key

                LOG "Safely resumed interrupted task and committed side effect"
        COMMIT TRANSACTION
```

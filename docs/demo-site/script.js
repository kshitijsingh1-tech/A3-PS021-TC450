/* ==========================================================================
   PS-021 Interactive Showcase -- Script
   Drives: nav toggle, retry trace, state machine walkthrough, 6 scenario sims
   ========================================================================== */

// ==================== NAV TOGGLE ====================
const navToggle = document.getElementById('navToggle');
const navMenu = document.getElementById('navMenu');
navToggle?.addEventListener('click', () => navMenu.classList.toggle('open'));
document.querySelectorAll('.navbar nav a').forEach(a =>
  a.addEventListener('click', () => navMenu.classList.remove('open'))
);

// ==================== INTERSECTION OBSERVER FOR REVEAL ====================
const revealEls = document.querySelectorAll('.card, .problem-card, .identity-card, .invariant-card, .arch-block, .scenario-info');
const revealObs = new IntersectionObserver((entries) => {
  entries.forEach(e => {
    if (e.isIntersecting) {
      e.target.classList.add('reveal', 'visible');
      revealObs.unobserve(e.target);
    }
  });
}, { threshold: 0.1 });
revealEls.forEach(el => {
  el.classList.add('reveal');
  revealObs.observe(el);
});

// ==================== UTILITY ====================
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
function randHex(len) { return [...Array(len)].map(() => Math.floor(Math.random()*16).toString(16)).join(''); }

// ==================== RETRY TRACE ====================
(function initRetryTrace() {
  const traceBtn = document.getElementById('retryTraceBtn');
  const resetBtn = document.getElementById('retryResetBtn');
  const traceContainer = document.getElementById('retryTrace');
  let attempt = 0;
  const dialogId = 'dlg_pay_q4';
  const idempKey = 'pay_inv_42';

  traceBtn?.addEventListener('click', () => {
    attempt++;
    const reqId = `req_${randHex(12)}`;
    const row = document.createElement('div');
    row.className = 'retry-row';
    row.innerHTML = `
      <span>${attempt}</span>
      <span class="stable">${dialogId}</span>
      <span class="stable">${idempKey}</span>
      <span class="dynamic">${reqId}</span>
    `;
    traceContainer.appendChild(row);
    row.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  });

  resetBtn?.addEventListener('click', () => {
    attempt = 0;
    traceContainer.querySelectorAll('.retry-row').forEach(r => r.remove());
  });
})();

// ==================== STATE MACHINE WALKTHROUGH ====================
(function initStateMachine() {
  const nodes = document.querySelectorAll('.sm-node');
  const logEl = document.getElementById('smLog');
  const normalBtn = document.getElementById('smNormalBtn');
  const crashBtn = document.getElementById('smCrashBtn');
  const resetBtn = document.getElementById('smResetBtn');
  let running = false;

  function clearAll() {
    nodes.forEach(n => n.classList.remove('active'));
    if (logEl) logEl.innerHTML = '';
  }

  function highlight(state) {
    nodes.forEach(n => n.classList.remove('active'));
    const target = document.querySelector(`.sm-node[data-state="${state}"]`);
    if (target) target.classList.add('active');
  }

  function log(from, to, detail) {
    if (!logEl) return;
    const entry = document.createElement('div');
    entry.className = 'log-entry';
    entry.innerHTML = `<span class="log-state" style="color: var(--state-${from.toLowerCase()})">${from}</span> <span class="log-arrow">-></span> <span class="log-state" style="color: var(--state-${to.toLowerCase()})">${to}</span> <span style="color: var(--ink-dim)">${detail}</span>`;
    logEl.appendChild(entry);
    logEl.scrollTop = logEl.scrollHeight;
  }

  async function runPath(steps) {
    if (running) return;
    running = true;
    clearAll();
    for (const step of steps) {
      await sleep(800);
      highlight(step.state);
      if (step.from) log(step.from, step.state, step.detail || '');
      else {
        const entry = document.createElement('div');
        entry.className = 'log-entry';
        entry.innerHTML = `<span class="log-state" style="color: var(--state-${step.state.toLowerCase()})">${step.state}</span> <span style="color: var(--ink-dim)">${step.detail || 'Initial state'}</span>`;
        logEl?.appendChild(entry);
      }
    }
    running = false;
  }

  normalBtn?.addEventListener('click', () => {
    runPath([
      { state: 'RECEIVED', detail: 'Registered in SQLite via BEGIN IMMEDIATE' },
      { state: 'ACCEPTED', from: 'RECEIVED', detail: 'Validated and queued' },
      { state: 'RUNNING', from: 'ACCEPTED', detail: 'Executing side effect' },
      { state: 'COMPLETED', from: 'RUNNING', detail: 'Committed atomically. Terminal state.' },
    ]);
  });

  crashBtn?.addEventListener('click', () => {
    runPath([
      { state: 'RECEIVED', detail: 'Registered in SQLite' },
      { state: 'ACCEPTED', from: 'RECEIVED', detail: 'Validated' },
      { state: 'RUNNING', from: 'ACCEPTED', detail: 'Executing...' },
      { state: 'RECOVERING', from: 'RUNNING', detail: '** CRASH DETECTED ** Process killed' },
      { state: 'RUNNING', from: 'RECOVERING', detail: 'Recovery Manager resumes execution' },
      { state: 'COMPLETED', from: 'RUNNING', detail: 'Side effect committed on second attempt' },
    ]);
  });

  resetBtn?.addEventListener('click', () => {
    running = false;
    clearAll();
  });
})();

// ==================== SCENARIO DATA ====================
const SCENARIOS = {
  1: {
    title: 'Scenario 1: Request Lost Before Delivery',
    desc: 'The first HTTP request never reaches Adapter B due to a network failure. Adapter A detects the timeout, generates a fresh request_id, and retransmits with the same dialog_id and idempotency_key. The second attempt succeeds through the normal lifecycle.',
    expected: 'COMPLETED on retry',
    effects: 'Exactly 1',
    dialogId: 'dlg_s1_demo',
    idempKey: 'idemp_s1_lost',
    steps: [
      { type: 'log', cls: 'log-adapter-a', text: 'Adapter A: Creating envelope (attempt=1)' },
      { type: 'envelope', attempt: 1 },
      { type: 'log', cls: 'log-adapter-a', text: 'Adapter A: Sending POST /dialog/task...' },
      { type: 'packet', dir: 'failed' },
      { type: 'a-state', text: 'TIMEOUT' },
      { type: 'log', cls: 'log-error', text: 'NETWORK: Request lost in transit! Timeout after 5s' },
      { type: 'log', cls: 'log-adapter-a', text: 'Adapter A: Retry policy triggered. Generating fresh request_id...' },
      { type: 'envelope', attempt: 2 },
      { type: 'a-state', text: 'RETRYING' },
      { type: 'log', cls: 'log-adapter-a', text: 'Adapter A: Sending POST /dialog/task (attempt=2)...' },
      { type: 'packet', dir: 'sending' },
      { type: 'b-state', text: 'PROCESSING' },
      { type: 'log', cls: 'log-adapter-b', text: 'Adapter B: Received. Atomic registration via BEGIN IMMEDIATE' },
      { type: 'db', status: 'RECEIVED', version: 1, effects: 0 },
      { type: 'log', cls: 'log-adapter-b', text: 'Adapter B: RECEIVED -> ACCEPTED -> RUNNING' },
      { type: 'db', status: 'RUNNING', version: 3, effects: 0 },
      { type: 'log', cls: 'log-adapter-b', text: 'Adapter B: Executing side effect + committing atomically' },
      { type: 'db', status: 'COMPLETED', version: 4, effects: 1 },
      { type: 'log', cls: 'log-success', text: 'Adapter B: COMPLETED. Side effect committed.' },
      { type: 'packet', dir: 'receiving' },
      { type: 'b-state', text: 'RESPONDED' },
      { type: 'log', cls: 'log-adapter-a', text: 'Adapter A: Received 200 COMPLETED. Task done.' },
      { type: 'a-state', text: 'DONE' },
      { type: 'result', pass: true },
    ]
  },
  2: {
    title: 'Scenario 2: Crash During Execution & Startup Recovery',
    desc: 'Adapter B crashes (os._exit) after accepting the task but before committing the side effect. On restart, the StartupRecoveryManager inspects interrupted operations, finds no committed side effect, resumes execution through RECOVERING -> RUNNING -> COMPLETED.',
    expected: 'COMPLETED after recovery',
    effects: 'Exactly 1',
    dialogId: 'dlg_s2_crash',
    idempKey: 'idemp_s2_crash',
    steps: [
      { type: 'log', cls: 'log-adapter-a', text: 'Adapter A: Creating envelope (attempt=1)' },
      { type: 'envelope', attempt: 1 },
      { type: 'log', cls: 'log-adapter-a', text: 'Adapter A: Sending POST /dialog/task...' },
      { type: 'packet', dir: 'sending' },
      { type: 'b-state', text: 'PROCESSING' },
      { type: 'log', cls: 'log-adapter-b', text: 'Adapter B: RECEIVED -> ACCEPTED -> RUNNING' },
      { type: 'db', status: 'RUNNING', version: 3, effects: 0 },
      { type: 'log', cls: 'log-error', text: '** CRASH ** os._exit(43) -- Process killed mid-execution!' },
      { type: 'packet', dir: 'crash' },
      { type: 'b-state', text: 'CRASHED' },
      { type: 'a-state', text: 'TIMEOUT' },
      { type: 'log', cls: 'log-warning', text: 'SERVER: Process terminated. SQLite transaction rolled back.' },
      { type: 'log', cls: 'log-info', text: 'SERVER: Restarting Adapter B...' },
      { type: 'b-state', text: 'BOOTING' },
      { type: 'log', cls: 'log-info', text: 'RECOVERY: StartupRecoveryManager scanning non-terminal ops...' },
      { type: 'log', cls: 'log-info', text: 'RECOVERY: Found 1 interrupted operation (status=RUNNING)' },
      { type: 'log', cls: 'log-info', text: 'RECOVERY: No committed side effect found. Safe to resume.' },
      { type: 'log', cls: 'log-adapter-b', text: 'RECOVERY: RUNNING -> RECOVERING -> RUNNING -> COMPLETED' },
      { type: 'db', status: 'COMPLETED', version: 7, effects: 1 },
      { type: 'b-state', text: 'RECOVERED' },
      { type: 'log', cls: 'log-success', text: 'RECOVERY: Task completed. Side effect committed. Identity preserved.' },
      { type: 'log', cls: 'log-adapter-a', text: 'Adapter A: Retry reaches recovered server. Gets 200 COMPLETED with stored result.' },
      { type: 'packet', dir: 'receiving' },
      { type: 'a-state', text: 'DONE' },
      { type: 'result', pass: true },
    ]
  },
  3: {
    title: 'Scenario 3: Response Lost After Successful Commit',
    desc: 'Adapter B successfully executes the side effect and commits COMPLETED, but the HTTP response is dropped before reaching Adapter A. On retry, Adapter B detects the duplicate idempotency_key, finds the operation already COMPLETED, and returns the stored result without re-executing.',
    expected: 'Deduplicated. Cached result returned.',
    effects: 'Exactly 1 (no duplicate)',
    dialogId: 'dlg_s3_lost_resp',
    idempKey: 'idemp_s3_resp',
    steps: [
      { type: 'log', cls: 'log-adapter-a', text: 'Adapter A: Sending POST /dialog/task (attempt=1)' },
      { type: 'envelope', attempt: 1 },
      { type: 'packet', dir: 'sending' },
      { type: 'b-state', text: 'PROCESSING' },
      { type: 'log', cls: 'log-adapter-b', text: 'Adapter B: RECEIVED -> ACCEPTED -> RUNNING -> COMPLETED' },
      { type: 'db', status: 'COMPLETED', version: 4, effects: 1 },
      { type: 'log', cls: 'log-success', text: 'Adapter B: Side effect committed. Sending response...' },
      { type: 'log', cls: 'log-error', text: 'NETWORK: HTTP response dropped! ConnectionResetError' },
      { type: 'packet', dir: 'failed' },
      { type: 'a-state', text: 'TIMEOUT' },
      { type: 'log', cls: 'log-adapter-a', text: 'Adapter A: Timeout. Retrying with fresh request_id (attempt=2)...' },
      { type: 'envelope', attempt: 2 },
      { type: 'packet', dir: 'sending' },
      { type: 'log', cls: 'log-adapter-b', text: 'Adapter B: Duplicate detected! idemp_key already COMPLETED.' },
      { type: 'log', cls: 'log-info', text: 'Adapter B: Returning stored result. No re-execution.' },
      { type: 'db', status: 'COMPLETED', version: 4, effects: 1 },
      { type: 'packet', dir: 'receiving' },
      { type: 'b-state', text: 'DEDUP' },
      { type: 'log', cls: 'log-adapter-a', text: 'Adapter A: Received 200 COMPLETED. Deduplication verified.' },
      { type: 'a-state', text: 'DONE' },
      { type: 'result', pass: true },
    ]
  },
  4: {
    title: 'Scenario 4: Concurrent Duplicate Requests',
    desc: 'Two identical requests with the same idempotency_key arrive simultaneously. SQLite BEGIN IMMEDIATE serializes the registrations: one thread creates the operation, the other detects a duplicate and returns the in-progress or completed result. Only one side effect is ever committed.',
    expected: 'Single operation created',
    effects: 'Exactly 1',
    dialogId: 'dlg_s4_concurrent',
    idempKey: 'idemp_s4_dup',
    steps: [
      { type: 'log', cls: 'log-adapter-a', text: 'Adapter A: Sending 2 concurrent requests with SAME idempotency_key' },
      { type: 'envelope', attempt: 1 },
      { type: 'log', cls: 'log-adapter-a', text: 'Thread 1: POST /dialog/task' },
      { type: 'log', cls: 'log-adapter-a', text: 'Thread 2: POST /dialog/task (same idemp_key, same payload)' },
      { type: 'packet', dir: 'sending' },
      { type: 'b-state', text: 'SERIALIZING' },
      { type: 'log', cls: 'log-adapter-b', text: 'Adapter B: BEGIN IMMEDIATE serializes both registrations' },
      { type: 'log', cls: 'log-adapter-b', text: 'Thread 1: Wins race. Creates new operation. RECEIVED -> ACCEPTED' },
      { type: 'db', status: 'ACCEPTED', version: 2, effects: 0 },
      { type: 'log', cls: 'log-adapter-b', text: 'Thread 2: INSERT hits UNIQUE constraint. Returns existing op.' },
      { type: 'log', cls: 'log-info', text: 'Thread 2: Operation in-progress. Returns 202 Accepted.' },
      { type: 'log', cls: 'log-adapter-b', text: 'Thread 1: RUNNING -> Side effect -> COMPLETED' },
      { type: 'db', status: 'COMPLETED', version: 4, effects: 1 },
      { type: 'packet', dir: 'receiving' },
      { type: 'b-state', text: 'DONE' },
      { type: 'log', cls: 'log-success', text: 'Result: Exactly 1 operation, exactly 1 side effect. Race safely serialized.' },
      { type: 'a-state', text: 'DONE' },
      { type: 'result', pass: true },
    ]
  },
  5: {
    title: 'Scenario 5: Mixed Completed & Pending Recovery',
    desc: 'Multiple operations exist at startup: some already COMPLETED with committed side effects, others interrupted mid-execution. The Recovery Manager handles each correctly -- reconciling committed effects without re-execution, and resuming interrupted work safely.',
    expected: 'All operations COMPLETED',
    effects: '4 total (one per operation)',
    dialogId: 'dlg_s5_mixed',
    idempKey: 'idemp_s5_*',
    steps: [
      { type: 'log', cls: 'log-info', text: 'SETUP: 4 operations in database at crash time' },
      { type: 'log', cls: 'log-success', text: 'Op 1 (idemp_s5_a): COMPLETED, side effect committed' },
      { type: 'log', cls: 'log-warning', text: 'Op 2 (idemp_s5_b): RUNNING, side effect committed (crash after commit)' },
      { type: 'log', cls: 'log-error', text: 'Op 3 (idemp_s5_c): ACCEPTED, no side effect (crash before exec)' },
      { type: 'log', cls: 'log-error', text: 'Op 4 (idemp_s5_d): RUNNING, no side effect (crash during exec)' },
      { type: 'log', cls: 'log-info', text: 'SERVER: Restarting Adapter B...' },
      { type: 'b-state', text: 'BOOTING' },
      { type: 'log', cls: 'log-info', text: 'RECOVERY: Found 3 non-terminal operations (Op 1 already terminal)' },
      { type: 'log', cls: 'log-adapter-b', text: 'RECOVERY Op 2: Side effect exists. RECONCILE -> COMPLETED (no re-exec)' },
      { type: 'log', cls: 'log-adapter-b', text: 'RECOVERY Op 3: No side effect. RECOVERING -> RUNNING -> COMPLETED' },
      { type: 'log', cls: 'log-adapter-b', text: 'RECOVERY Op 4: No side effect. RECOVERING -> RUNNING -> COMPLETED' },
      { type: 'db', status: 'ALL COMPLETED', version: '--', effects: 4 },
      { type: 'b-state', text: 'RECOVERED' },
      { type: 'log', cls: 'log-success', text: 'RECOVERY: All 4 operations COMPLETED. 4 side effects total. Identity preserved.' },
      { type: 'a-state', text: 'DONE' },
      { type: 'result', pass: true },
    ]
  },
  6: {
    title: 'Scenario 6: Idempotency Key Payload Conflict',
    desc: 'A client reuses the same idempotency_key but sends a different payload (different amount). Adapter B detects the SHA-256 payload hash mismatch and rejects with HTTP 409 Conflict, protecting against accidental misuse of idempotency keys.',
    expected: 'HTTP 409 Conflict',
    effects: 'Original preserved',
    dialogId: 'dlg_s6_conflict',
    idempKey: 'idemp_s6_conflict',
    steps: [
      { type: 'log', cls: 'log-adapter-a', text: 'Adapter A: First request -- payload: {amount: 100.0}' },
      { type: 'envelope', attempt: 1 },
      { type: 'packet', dir: 'sending' },
      { type: 'b-state', text: 'PROCESSING' },
      { type: 'log', cls: 'log-adapter-b', text: 'Adapter B: Registered. payload_hash = sha256({amount:100.0})' },
      { type: 'db', status: 'COMPLETED', version: 4, effects: 1 },
      { type: 'log', cls: 'log-success', text: 'Adapter B: COMPLETED successfully.' },
      { type: 'packet', dir: 'receiving' },
      { type: 'a-state', text: 'DONE' },
      { type: 'log', cls: 'log-adapter-a', text: 'Adapter A: Second request -- SAME idemp_key, DIFFERENT payload: {amount: 999.0}' },
      { type: 'envelope', attempt: 2 },
      { type: 'packet', dir: 'sending' },
      { type: 'log', cls: 'log-adapter-b', text: 'Adapter B: idempotency_key exists. Comparing payload hashes...' },
      { type: 'log', cls: 'log-error', text: 'Adapter B: HASH MISMATCH! sha256({amount:999.0}) != sha256({amount:100.0})' },
      { type: 'log', cls: 'log-error', text: 'Adapter B: HTTP 409 CONFLICT -- IdempotencyConflictError raised' },
      { type: 'packet', dir: 'crash' },
      { type: 'b-state', text: '409 CONFLICT' },
      { type: 'log', cls: 'log-warning', text: 'Adapter A: Received 409 Conflict. Original operation preserved.' },
      { type: 'a-state', text: 'CONFLICT' },
      { type: 'db', status: 'COMPLETED', version: 4, effects: 1 },
      { type: 'result', pass: true },
    ]
  }
};

// ==================== SCENARIO ENGINE ====================
(function initScenarios() {
  const tabs = document.querySelectorAll('.scenario-tab');
  const titleEl = document.getElementById('scenarioTitle');
  const descEl = document.getElementById('scenarioDesc');
  const expectedEl = document.getElementById('scenarioExpected');
  const effectsEl = document.getElementById('scenarioEffects');
  const runBtn = document.getElementById('simRunBtn');
  const resetBtn = document.getElementById('simResetBtn');
  const logEl = document.getElementById('simLog');
  const logStatus = document.getElementById('simLogStatus');
  const resultEl = document.getElementById('scenarioResult');

  // Sim elements
  const simAState = document.getElementById('simAdapterAState');
  const simBState = document.getElementById('simAdapterBState');
  const simDialogId = document.getElementById('simDialogId');
  const simIdempKey = document.getElementById('simIdempKey');
  const simReqId = document.getElementById('simReqId');
  const simAttempt = document.getElementById('simAttempt');
  const simPacket = document.getElementById('simPacket');
  const simWireLabel = document.getElementById('simWireLabel');
  const simDbStatus = document.getElementById('simDbStatus');
  const simDbVersion = document.getElementById('simDbVersion');
  const simDbEffects = document.getElementById('simDbEffects');

  let currentScenario = 1;
  let simRunning = false;

  function loadScenario(num) {
    currentScenario = num;
    const s = SCENARIOS[num];
    tabs.forEach(t => t.classList.toggle('active', parseInt(t.dataset.scenario) === num));
    if (titleEl) titleEl.textContent = s.title;
    if (descEl) descEl.textContent = s.desc;
    if (expectedEl) expectedEl.textContent = s.expected;
    if (effectsEl) effectsEl.textContent = s.effects;
    resetSim();
  }

  function resetSim() {
    simRunning = false;
    if (simAState) simAState.textContent = 'IDLE';
    if (simBState) simBState.textContent = 'LISTENING';
    const s = SCENARIOS[currentScenario];
    if (simDialogId) simDialogId.textContent = s.dialogId;
    if (simIdempKey) simIdempKey.textContent = s.idempKey;
    if (simReqId) simReqId.textContent = '--';
    if (simAttempt) simAttempt.textContent = '0';
    if (simPacket) { simPacket.className = 'sim-packet'; }
    if (simWireLabel) simWireLabel.textContent = '';
    if (simDbStatus) simDbStatus.textContent = '--';
    if (simDbVersion) simDbVersion.textContent = '--';
    if (simDbEffects) simDbEffects.textContent = '0';
    if (logEl) logEl.innerHTML = '';
    if (logStatus) { logStatus.textContent = 'Ready'; logStatus.className = 'sim-log-status'; }
    if (resultEl) { resultEl.textContent = ''; resultEl.className = 'scenario-result'; }
    if (runBtn) runBtn.disabled = false;
  }

  async function runScenario() {
    if (simRunning) return;
    simRunning = true;
    if (runBtn) runBtn.disabled = true;
    if (logStatus) { logStatus.textContent = 'Running'; logStatus.className = 'sim-log-status running'; }

    const steps = SCENARIOS[currentScenario].steps;
    let currentReqId = '';
    let t = 0;

    for (const step of steps) {
      if (!simRunning) break;
      t++;
      const ts = String(t).padStart(2, '0');

      switch (step.type) {
        case 'log': {
          const line = document.createElement('div');
          line.className = 'log-line';
          line.innerHTML = `<span class="log-time">[${ts}]</span> <span class="${step.cls}">${step.text}</span>`;
          logEl?.appendChild(line);
          logEl.scrollTop = logEl.scrollHeight;
          await sleep(500);
          break;
        }
        case 'envelope': {
          currentReqId = `req_${randHex(12)}`;
          if (simReqId) simReqId.textContent = currentReqId;
          if (simAttempt) simAttempt.textContent = step.attempt;
          if (simAState) simAState.textContent = `SENDING (attempt=${step.attempt})`;
          await sleep(300);
          break;
        }
        case 'packet': {
          if (simPacket) {
            simPacket.className = 'sim-packet';
            // Force reflow
            void simPacket.offsetWidth;
            simPacket.classList.add(step.dir);
          }
          if (step.dir === 'sending') { if (simWireLabel) simWireLabel.textContent = 'POST ->'; }
          else if (step.dir === 'receiving') { if (simWireLabel) simWireLabel.textContent = '<- 200'; }
          else if (step.dir === 'failed') { if (simWireLabel) simWireLabel.textContent = 'X LOST'; }
          else if (step.dir === 'crash') { if (simWireLabel) simWireLabel.textContent = 'X CRASH'; }
          await sleep(900);
          break;
        }
        case 'a-state': {
          if (simAState) {
            simAState.textContent = step.text;
            simAState.classList.add('state-pulse');
            setTimeout(() => simAState.classList.remove('state-pulse'), 1000);
          }
          await sleep(400);
          break;
        }
        case 'b-state': {
          if (simBState) {
            simBState.textContent = step.text;
            simBState.classList.add('state-pulse');
            setTimeout(() => simBState.classList.remove('state-pulse'), 1000);
          }
          await sleep(400);
          break;
        }
        case 'db': {
          if (simDbStatus) simDbStatus.textContent = step.status;
          if (simDbVersion) simDbVersion.textContent = step.version;
          if (simDbEffects) simDbEffects.textContent = step.effects;
          await sleep(400);
          break;
        }
        case 'result': {
          if (resultEl) {
            resultEl.textContent = step.pass ? 'PASS' : 'FAIL';
            resultEl.className = `scenario-result ${step.pass ? 'pass' : 'fail'}`;
          }
          if (logStatus) {
            logStatus.textContent = step.pass ? 'Passed' : 'Failed';
            logStatus.className = `sim-log-status ${step.pass ? 'pass' : 'fail'}`;
          }
          break;
        }
      }
    }
    simRunning = false;
  }

  tabs.forEach(tab => {
    tab.addEventListener('click', () => {
      loadScenario(parseInt(tab.dataset.scenario));
    });
  });

  runBtn?.addEventListener('click', runScenario);
  resetBtn?.addEventListener('click', resetSim);

  // Initialize first scenario
  loadScenario(1);
})();

// ==================== SMOOTH SCROLL ACTIVE NAV ====================
const sections = document.querySelectorAll('section[id]');
const navLinks = document.querySelectorAll('.navbar nav a');

window.addEventListener('scroll', () => {
  let current = '';
  sections.forEach(s => {
    const top = s.offsetTop - 100;
    if (pageYOffset >= top) current = s.getAttribute('id');
  });
  navLinks.forEach(a => {
    a.style.color = '';
    if (a.getAttribute('href') === `#${current}`) a.style.color = 'var(--accent)';
  });
});

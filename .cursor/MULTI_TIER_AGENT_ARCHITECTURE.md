# Multi-Tier Agent Architecture — Autonomous GitFlow for Maestro

> "The infinite music machine, continuously shipping."

This document specifies a fully autonomous, industrial-strength, self-healing,
deterministic GitFlow engine that continuously converts GitHub issues into
reviewed, tested, merged code — with human oversight reduced to periodic
heartbeat approvals and issue creation.

---

## 1. Vision

You create issues. The machine does everything else.

```
Human
  └─ creates GitHub issues (hundreds at a time, any rate)
  └─ reviews periodic heartbeat reports
  └─ approves cycle continuation (green = auto-continue)

Machine (always running)
  ├─ Triage Manager    → Issue Agents      → well-formed GitHub issues
  ├─ Build Manager     → Impl Agents       → reviewed PRs
  └─ Merge Manager     → Review Agents     → merged code on dev
       └─ All three managers cross-check each other's artifacts
       └─ Heartbeat pulse emitted after every N merges
       └─ Supervisor monitors all three managers and restarts on crash
```

One human input loop. One continuous machine loop.

---

## 2. Architecture — Four Tiers

```
┌─────────────────────────────────────────────────────────────────────┐
│  TIER 0 — Human                                                     │
│  Creates issues. Reviews heartbeats. Approves or pauses cycles.    │
│  The only entity that can override a RED status.                   │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ runs supervisor_kickoff.sh (once)
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  TIER 1 — Supervisor Agent (1 instance, long-lived)                │
│  • Spawns and monitors 3 manager agents                            │
│  • Detects stalled / crashed managers via lease expiry             │
│  • Restarts failed managers with new lease token                   │
│  • Emits system-level heartbeat (all-stage KPI rollup)             │
│  • The only agent that may escalate to the human                   │
└───────┬──────────────────────┬───────────────────────┬─────────────┘
        │                      │                       │
        ▼                      ▼                       ▼
┌───────────────┐   ┌──────────────────┐   ┌──────────────────────┐
│ TIER 2        │   │ TIER 2           │   │ TIER 2               │
│ Triage Mgr    │   │ Build Mgr        │   │ Merge Mgr            │
│               │   │                  │   │                      │
│ needs-triage  │   │ ready-for-impl   │   │ ready-for-review     │
│     ↓         │   │      ↓           │   │       ↓              │
│ ready-for-impl│   │ ready-for-review │   │ merged / rejected    │
│               │   │                  │   │                      │
│ Acquires lease│   │ Acquires lease   │   │ Acquires lease       │
│ Validates     │   │ Validates        │   │ Validates            │
│ Cross-checks  │   │ Cross-checks     │   │ Cross-checks         │
│ peers on#COORD│   │ peers on #COORD  │   │ peers on #COORD      │
└──────┬────────┘   └────────┬─────────┘   └──────────┬───────────┘
       │  spawns ≤4          │  spawns ≤4              │  spawns ≤4
       ▼                     ▼                         ▼
┌────────────┐         ┌────────────┐          ┌────────────┐
│ TIER 3     │ × N     │ TIER 3     │ × N      │ TIER 3     │ × N
│ Triage     │         │ Impl       │          │ Review     │
│ Workers    │         │ Workers    │          │ Workers    │
│            │         │            │          │            │
│ Ephemeral  │         │ Ephemeral  │          │ Ephemeral  │
│ worktree   │         │ worktree   │          │ worktree   │
│ Self-destructs       │ Self-destructs        │ Self-destructs
└────────────┘         └────────────┘          └────────────┘

GitHub is the canonical state store for ALL tiers.
#REGISTRY  #COORD  #HEARTBEAT  #LEASE  are permanent issues.
```

The control plane library (`agent_engine/`) enforces invariants at every tier.
No manager or worker may bypass it.

---

## 3. Control Plane Library — `agent_engine/`

The control plane is a Python package that runs on the **host** (not in Docker),
because it orchestrates Docker, git worktrees, and the GitHub CLI. It is not
part of the Maestro application. All modules expose both a library API and a
CLI interface so AI agents can invoke them as shell commands.

```
agent_engine/
  __init__.py
  types.py                 ← shared enums, dataclasses, type aliases
  workflow_state_machine.py ← formal state transition enforcement
  agent_registry.py        ← SHA-256 hash-chained canonical state store
  lease_manager.py         ← distributed lease via GitHub issue comments
  stage_artifact.py        ← Pydantic cross-manager artifact contract
  rolling_metrics.py       ← rolling KPI window with trend/slope analysis
  severity.py              ← failure severity classification (S1–S4)
  concurrency.py           ← token bucket for worker dispatch throttling
  resource_monitor.py      ← Docker CPU/memory + worktree count guardrails
  replay.py                ← deterministic audit-trail replay (read-only)
  supervisor.py            ← supervisor lifecycle and watchdog logic
  tests/
    conftest.py
    test_registry.py
    test_state_machine.py
    test_metrics.py
    test_concurrency.py
    test_failure_injection.py
    test_integration.py
```

---

## 4. Phase 1 — Registry Hardening (`agent_registry.py`)

### Purpose

Replaces the ad-hoc JSON comment format with a formally verified, hash-chained
registry. No manager may mutate state directly. All mutations go through the
registry writer, which enforces the chain and rejects split-brain writes.

### Schema v2.0

```json
{
  "schema_version": "2.0",
  "cycle": 7,
  "registry_hash": "<sha256 of this document, excluding this field>",
  "prev_registry_hash": "<sha256 of the previous registry comment>",
  "updated_at": "2026-02-28T01:45:00Z",
  "updated_by": "merge-manager-cycle-7",
  "tasks": [
    {
      "issue_number": 140,
      "phase": "merged",
      "branch": "feat/muse-harmony",
      "pull_request_number": 150,
      "grade": "A",
      "merged_at": "2026-02-28T01:44:00Z",
      "reject_reason": null,
      "attempt_counters": {
        "triage_attempts": 1,
        "impl_attempts": 1,
        "review_attempts": 1
      }
    }
  ]
}
```

### Key invariants

- `registry_hash` is computed over the full document with that field set to `""`.
- A write is rejected if the writer's `prev_registry_hash` does not match the
  current latest registry's `registry_hash`. This makes concurrent writes
  impossible — the second writer always loses and enters reconciliation.
- A task's `phase` may only advance forward per `ALLOWED_TRANSITIONS`.
  A backward transition requires a human override label `force-reopen` on the
  GitHub issue.
- `attempt_counters` enforce the loop guard: any counter > 3 triggers
  `LOOP_GUARD_TRIGGERED` and immediate escalation to the Supervisor.

### Core functions

```python
def compute_registry_hash(registry: dict) -> str
    # SHA-256 of canonical JSON (sorted keys, no whitespace), registry_hash field set to ""

def validate_registry_transition(prev: dict, next: dict) -> bool
    # Every task that changed phase must satisfy ALLOWED_TRANSITIONS
    # attempt_counters may only increase
    # cycle must be >= prev cycle

def load_latest_registry() -> dict
    # gh api /repos/{owner}/{repo}/issues/{REGISTRY_ISSUE}/comments
    # Find the most recent comment matching schema_version: "2.0"
    # Verify registry_hash matches the document
    # Return parsed dict

def write_registry_update(new_registry: dict, prev_hash: str) -> None
    # Verify prev_hash matches current latest
    # If mismatch → raise RegistryConflictError (caller enters reconciliation)
    # Compute and inject registry_hash
    # gh issue comment {REGISTRY_ISSUE} --body "<JSON>"

def reconcile_registry(local: dict, remote: dict) -> dict
    # Merge strategy: take the later phase for each issue_number
    # Raise ReconciliationError if phases are contradictory (e.g. both merged different PRs)
```

### Reconciliation mode

When a manager detects a hash mismatch (another manager wrote first), it:
1. Loads the current remote registry
2. Diffs its intended write against the remote state
3. If no task has contradictory phases → merges and retries write
4. If contradiction found → posts `RECONCILIATION_FAILED` on `#COORD` and escalates to Supervisor

---

## 5. Phase 2 — Distributed Lease Lock (`lease_manager.py`)

### Purpose

Prevents two instances of the same manager from running concurrently for the
same cycle. A crashed manager's lease expires, allowing the Supervisor to
relaunch a fresh manager that inherits the work.

### Lease backing store

A permanent GitHub issue (`#LEASE`) stores lease tokens as structured comments:

```
LEASE_TOKEN
stage: build
cycle: 7
acquired_at: 2026-02-28T01:00:00Z
expires_at: 2026-02-28T01:05:00Z
manager_id: build-manager-7-a3f2
status: HELD
```

### Manager lease lifecycle

```
ACQUIRE  — post LEASE_TOKEN comment on #LEASE for this stage + cycle
           if a non-expired token for this stage+cycle already exists → CONFLICT
           CONFLICT means another manager is already running → abort

RENEW    — post updated LEASE_TOKEN every 5 minutes with new expires_at
           if renewal fails (GitHub error) → treat as crash, enter cleanup

RELEASE  — post LEASE_TOKEN with status: RELEASED when work is complete
```

### Supervisor lease responsibilities

```
POLL     — every 2 minutes, read #LEASE for all three stages
EXPIRED  — a lease is expired if expires_at < now AND status != RELEASED
RESTART  — if expired: post LEASE_TOKEN with status: SUPERVISOR_RECLAIMED
           then relaunch the manager for that stage
STAGNANT — if a HELD lease is being renewed but no registry writes for > 15 min
           → post STAGNATION_DETECTED on #COORD, evaluate severity
```

### Conflict detection

A lease conflict (two managers for the same stage/cycle) means the kickoff
script was run twice or a manager was restarted without lease release. The
second manager detects the conflict, refuses to proceed, and posts a
`LEASE_CONFLICT` severity S2 event on `#COORD`.

---

## 6. Phase 3 — Formal State Machine (`workflow_state_machine.py`)

### State enum

```python
class WorkflowState(str, Enum):
    NEEDS_TRIAGE              = "needs-triage"
    READY_FOR_IMPLEMENTATION  = "ready-for-implementation"
    IN_PROGRESS               = "in-progress"
    READY_FOR_REVIEW          = "ready-for-review"
    IN_REVIEW                 = "in-review"
    GRADE_REJECTED            = "grade-rejected"
    MERGED                    = "merged"
    CLOSED                    = "closed"
```

### Allowed transitions

```python
ALLOWED_TRANSITIONS: dict[WorkflowState, set[WorkflowState]] = {
    WorkflowState.NEEDS_TRIAGE:             {WorkflowState.READY_FOR_IMPLEMENTATION},
    WorkflowState.READY_FOR_IMPLEMENTATION: {WorkflowState.IN_PROGRESS},
    WorkflowState.IN_PROGRESS:              {WorkflowState.READY_FOR_REVIEW,
                                             WorkflowState.READY_FOR_IMPLEMENTATION},
    WorkflowState.READY_FOR_REVIEW:         {WorkflowState.IN_REVIEW},
    WorkflowState.IN_REVIEW:               {WorkflowState.MERGED,
                                             WorkflowState.GRADE_REJECTED},
    WorkflowState.GRADE_REJECTED:           {WorkflowState.READY_FOR_IMPLEMENTATION},
    WorkflowState.MERGED:                   {WorkflowState.CLOSED},
    WorkflowState.CLOSED:                   set(),  # terminal
}
```

`IN_PROGRESS → READY_FOR_IMPLEMENTATION` is the re-queue path for branch
creation failures. All other backward transitions are forbidden without the
`force-reopen` label and a human comment on the issue.

### Enforcement

```python
def validate_transition(current: WorkflowState, next: WorkflowState) -> None
    # Raises InvalidTransitionError if next not in ALLOWED_TRANSITIONS[current]

def apply_transition(issue_number: int, next_state: WorkflowState,
                     prev_registry_hash: str) -> None
    # 1. Load current state from registry
    # 2. validate_transition(current, next_state)
    # 3. Update GitHub label via gh issue edit
    # 4. write_registry_update with new phase
    # Atomic: if step 4 fails, the label change is rolled back
```

Every manager calls `apply_transition` instead of `gh issue edit` directly.
Label changes that bypass this function are a protocol violation.

---

## 7. Phase 4 — Cross-Manager Artifact Contracts (`stage_artifact.py`)

### Purpose

Every manager must emit a structured artifact at the end of its cycle phase.
Peer managers validate the artifact before consuming the output as their input.
A hash mismatch or schema violation blocks the downstream stage.

### Artifact schema (Pydantic v2)

```python
class StageArtifact(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    cycle: int
    stage: Stage                     # "triage" | "build" | "merge"
    manager_id: str
    produced_at: str                 # ISO-8601
    input_items: list[int]           # issue or PR numbers consumed
    output_items: list[int]          # issue or PR numbers produced
    artifact_hash: str               # SHA-256 of the artifact (self-referential, "" during compute)
    status: Literal["ok", "partial", "failed"]
    notes: str = ""
```

### Cross-manager validation protocol

```
Triage Manager emits artifact → Build Manager validates before pulling new issues
Build Manager emits artifact  → Merge Manager validates before pulling new PRs
Merge Manager emits artifact  → Triage Manager validates before starting next cycle

Validation checks:
  1. artifact_hash matches document
  2. stage matches expected upstream stage
  3. cycle matches current cycle
  4. output_items on upstream matches my expected input_items
  5. status == "ok" (partial → proceed with warning; failed → RED)
```

Artifacts are posted as comments on `#COORD` immediately after each cycle phase.

---

## 8. Phase 5 — Supervisor Layer (`supervisor.py`)

### Architecture

The Supervisor is the **only agent manually launched** by the human. It is the
Tier 1 node and the top of the agent hierarchy. It spawns the three managers as
Tier 2 Task sub-agents and monitors them for the duration of the cycle.

### Responsibilities

```
SPAWN      — launch Triage, Build, and Merge managers as sub-agents
             inject: REGISTRY_ISSUE, COORD_ISSUE, HEARTBEAT_ISSUE, LEASE_ISSUE, CYCLE
MONITOR    — poll #LEASE every 2 minutes for expired leases
RESTART    — on lease expiry: reclaim lease, relaunch manager sub-agent
STAGNATION — if manager holds lease but no registry write for > 15 min:
             evaluate severity → pause stage or full system
HEARTBEAT  — emit system-level heartbeat on #HEARTBEAT after every N merges
             (delegates KPI computation to rolling_metrics)
SHUTDOWN   — on RED status: stop spawning new cycles, await human resume
```

### Lifecycle states

```
SPAWNED  → RUNNING  → STALLED  → RECOVERING → COMPLETED
                    ↘                         ↗
                      FAILED  → ESCALATED
```

Each manager reports its lifecycle state as a structured comment on `#COORD`.
The Supervisor reads `#COORD` to determine the current state of all managers.

### Watchdog chain

The Supervisor itself has a heartbeat timeout. If the Supervisor's own
heartbeat on `#HEARTBEAT` stops updating, the human is the final watchdog.
This is by design: there is no automated recovery above the Supervisor tier.

---

## 9. Phase 6 — Resource Guardrails (`resource_monitor.py`)

### Purpose

Prevent Docker resource exhaustion and worktree accumulation from degrading
the host system. Managers must consult the resource monitor before dispatching
each batch of workers.

### Checks

```python
def check_docker_cpu_percent(container: str = "maestro-app") -> float
    # docker stats --no-stream --format "{{.CPUPerc}}" <container>
    # Returns 0.0–100.0

def check_docker_memory_mb(container: str = "maestro-app") -> float
    # docker stats --no-stream --format "{{.MemUsage}}" <container>
    # Parses "1.2GiB / 8GiB" → returns current usage in MB

def count_active_worktrees(repo_root: str) -> int
    # git -C repo_root worktree list --porcelain | grep "^worktree" | wc -l
    # Returns count (main repo counts as 1, subtract 1 for workers only)

def check_dispatch_allowed(
    max_cpu_percent: float = 80.0,
    max_memory_mb: float = 6144.0,
    max_worktrees: int = 24,
) -> DispatchDecision
    # Returns: DispatchDecision(allowed=True/False, reason="", throttle_to=N)
    # throttle_to: suggested max_workers for this dispatch (may be < requested)
```

### Dispatch decision

```python
@dataclass
class DispatchDecision:
    allowed: bool
    reason: str          # empty string if allowed
    throttle_to: int     # max workers to spawn right now (0 = do not dispatch)
```

If `allowed=False`, the manager waits 60 seconds and re-checks before aborting
the cycle. Three consecutive `allowed=False` results → Severity S3 escalation.

---

## 10. Phase 7 — Deterministic Replay (`replay.py`)

### Purpose

Given a cycle number N, reconstruct the full execution sequence from the
Registry and GitHub comment history. Produces a replay report that validates
integrity and detects drift between the Registry and actual GitHub state.

### Replay mode rules

- **Read-only.** No GitHub mutations. No registry writes. No label changes.
- All output goes to stdout or a specified `--output` file.
- Exit code 0 = integrity validated. Exit code 1 = drift detected.

### CLI

```bash
python -m agent_engine.replay --cycle 7
python -m agent_engine.replay --cycle 7 --output /tmp/replay-7.json
python -m agent_engine.replay --cycle 7 --format text   # human-readable
```

### Replay output

```json
{
  "cycle": 7,
  "replayed_at": "2026-02-28T03:00:00Z",
  "registry_snapshot": { ... },
  "github_state": {
    "140": { "state": "closed", "pr": "merged" },
    "141": { "state": "open",   "pr": "open", "labels": ["grade-C"] }
  },
  "transition_graph": [
    { "issue": 140, "from": "needs-triage", "to": "ready-for-implementation", "at": "..." },
    { "issue": 140, "from": "ready-for-implementation", "to": "in-progress", "at": "..." },
    ...
  ],
  "drift_report": {
    "total_issues": 4,
    "clean": 3,
    "drifted": 1,
    "drift_details": [
      { "issue": 141, "registry_phase": "merged", "github_state": "open",
        "verdict": "DRIFT — registry claims merged but PR is still open" }
    ]
  },
  "integrity": "FAIL"
}
```

### When to run replay

- After any RED heartbeat, before resuming
- After a Supervisor crash-recovery
- On demand by the human at any time
- As part of the certification test suite

---

## 11. Phase 8 — Advanced KPI Derivation (`rolling_metrics.py`)

### Rolling window

```python
class RollingMetrics:
    window_size: int = 20   # last N cycles

    def update(self, cycle_metrics: CycleMetrics) -> None
        # Append to ring buffer; evict oldest if full

    def compute_trend(self, metric: str) -> float
        # Linear regression slope over the window for the named metric
        # Positive = improving, negative = degrading

    def compute_health_status(self) -> HealthStatus
        # GREEN / YELLOW / RED based on thresholds + trend
```

### Trend-based early warning

The heartbeat YELLOW status fires when a **trend** crosses a threshold,
not just the raw value. This gives one full cycle of advance warning before RED.

```
Grade C/D/F rate:
  raw  > 25%           → RED immediately
  trend slope > 0.02   → YELLOW (degrading, not yet RED)
  raw  < 10%, flat     → GREEN

Mypy clean rate:
  raw  < 95%           → RED
  trend slope < -0.01  → YELLOW
  100%, flat           → GREEN

review_to_merge_minutes:
  raw  > 45 min        → RED
  trend slope > 1.0    → YELLOW (each cycle taking ~1 min longer)
  < 20 min, flat       → GREEN
```

### Heartbeat slope analysis output

```
KPI Trend Analysis (last 20 cycles):
  grade_c_rate:           0.04  slope: +0.005 ↗  [YELLOW — rising]
  mypy_clean_rate:        1.00  slope:  0.000 →  [GREEN]
  review_to_merge_min:   11.2   slope: -0.3   ↘  [GREEN — improving]
  loop_guard_triggers:    0     slope:  0.000 →  [GREEN]
```

---

## 12. Phase 9 — Failure Severity Codes (`severity.py`)

### Severity enum

```python
class Severity(str, Enum):
    S1 = "transient"         # Retry automatically. No human needed.
    S2 = "retry_mutation"    # Retry with strategy change. Log visibly.
    S3 = "human_required"    # Pause this stage. Post on #COORD. Await human.
    S4 = "integrity_risk"    # Halt entire system. Emergency escalation.
```

### Severity decision matrix

| Event | Severity | Supervisor action |
|-------|----------|------------------|
| GitHub API timeout | S1 | Retry after 30s |
| Lease renewal failure | S1 | Retry immediately |
| Registry hash mismatch (reconcilable) | S2 | Reconcile and retry write |
| Loop guard triggered (attempt > 3) | S3 | Pause stage, post on #COORD |
| Worker orphaned (worktree cleanup failed) | S2 | Force prune, log |
| Registry drift detected (replay) | S3 | Pause stage, run replay |
| Contradictory registry phases | S4 | Halt system, alert human |
| Double PR detected | S4 | Halt merge stage, alert human |
| Lease conflict (duplicate manager) | S2 | Second manager aborts |
| Resource guardrail exhausted (3× denied) | S3 | Pause stage |
| Supervisor's own heartbeat missed | S4 | Human is the watchdog |

### Escalation format (posted on #COORD)

```
ESCALATION
severity: S3
event: loop_guard_triggered
stage: build
issue: 141
cycle: 7
manager_id: build-manager-7-a3f2
detail: impl_attempts counter reached 4 without phase advancement
action_taken: stage paused
awaiting: human comment "resume build" or "close 141"
timestamp: 2026-02-28T02:15:00Z
```

---

## 13. Phase 10 — Concurrency Discipline (`concurrency.py`)

### Token bucket

Controls the rate at which managers dispatch worker agents. Prevents any
stage from saturating Docker's test runner capacity.

```python
class TokenBucket:
    capacity: int          # max concurrent workers across all stages
    tokens: int            # current available slots
    refill_rate: float     # tokens restored per minute (as workers complete)

    def acquire(self, n: int = 1) -> bool
        # True if n tokens available. Deducts n if so. Non-blocking.

    def release(self, n: int = 1) -> None
        # Return n tokens when workers complete

    def wait_for_token(self, timeout_seconds: float = 300.0) -> bool
        # Blocks until a token is available or timeout. Returns False on timeout.
```

### Global concurrency limits (defaults, overridable via env vars)

```
AGENT_MAX_WORKERS_PER_STAGE=4     # max worker sub-agents per manager
AGENT_MAX_WORKERS_TOTAL=12        # global cap across all three stages
AGENT_MAX_WORKTREES=24            # max git worktrees (including issue- and pr- prefixed)
AGENT_MAX_DOCKER_RUNS=8           # max simultaneous mypy/pytest docker execs
```

### Worktree namespace isolation

Each worker is assigned a worktree in a deterministic namespace:
```
~/.cursor/worktrees/<repo>/issue-<N>    ← Triage + Build workers
~/.cursor/worktrees/<repo>/pr-<N>       ← Merge workers
```

No worker may create a worktree outside its namespace. The resource monitor
validates this by listing all worktrees and rejecting unknown prefixes.

### Cleanup validation

After every cycle, the Supervisor runs:
```bash
git -C "$REPO" worktree list --porcelain | grep "^worktree"
```

Any worktree in the namespace from a completed cycle is an orphan.
Orphans are force-pruned and logged as Severity S2.

---

## 14. Three Pipeline Stages (Updated)

### Stage 1 — Triage (raw input → well-formed issue)

**Manager:** Triage Manager  
**Workers:** Issue Triage Agents (one per raw issue)  
**Input label:** `needs-triage`  
**Output label:** `ready-for-implementation`

Manager cycle:
1. Acquire lease on `#LEASE` for `stage=triage, cycle=N`
2. Poll `gh issue list --label needs-triage`
3. Check Registry — skip any already past `needs-triage`
4. Check resource monitor — get `throttle_to`
5. Dispatch ≤`throttle_to` Triage Workers as Task sub-agents
6. Collect results — verify each issue now has `ready-for-implementation` label
7. Validate no file-overlap across enriched issues
8. Emit `StageArtifact` on `#COORD`
9. Write registry update (hash-chained)
10. Renew lease or release if done
11. Validate peer artifacts from Build + Merge before next cycle

### Stage 2 — Build (issue → PR)

**Manager:** Build Manager  
**Workers:** Implementation Agents (PARALLEL_ISSUE_TO_PR.md protocol)  
**Input label:** `ready-for-implementation`  
**Output label:** `ready-for-review`

Manager cycle:
1. Acquire lease; validate Triage artifact for this cycle
2. Poll issues; check Registry for `in-progress` guard (idempotency)
3. Resource check → throttle dispatch
4. Mark issues `in-progress` via `apply_transition` (state machine enforced)
5. Dispatch workers; collect PR URLs
6. Validate PRs exist on GitHub; verify `ready-for-review` label
7. Emit artifact; write registry; release lease

### Stage 3 — Merge (PR → dev)

**Manager:** Merge Manager  
**Workers:** Review Agents (PARALLEL_PR_REVIEW.md protocol)  
**Input label:** `ready-for-review`  
**Output label:** `merged` / `grade-rejected`

Manager cycle:
1. Acquire lease; validate Build artifact for this cycle
2. Poll PRs; check Registry (skip already merged)
3. Resource check → throttle dispatch
4. Mark PRs `in-review` via `apply_transition`
5. Dispatch workers; collect grades + merge status
6. Grade B → confirm follow-up issue filed (required artifact)
7. Grade C/D/F → apply `grade-rejected`, re-queue issue to `ready-for-implementation`
8. Emit artifact; write registry; release lease
9. Trigger heartbeat computation if N merges reached

---

## 15. State Machine (Complete)

Every issue moves through exactly these phases. No backward transitions
without `force-reopen` label + human comment.

```
needs-triage
    │ (Triage Manager)
    ▼
ready-for-implementation
    │ (Build Manager — marks in-progress)
    ▼
in-progress
    │ (Build worker — implements + opens PR)
    ▼
ready-for-review
    │ (Merge Manager — marks in-review)
    ▼
in-review
    │
    ├──▶ grade-rejected  ──▶ ready-for-implementation  (re-queue)
    │
    ▼
merged
    │ (Merge Manager — closes issue)
    ▼
closed  [terminal]
```

Label set per phase:

| Phase | Labels present |
|-------|---------------|
| needs-triage | `needs-triage` |
| ready-for-implementation | `ready-for-implementation` |
| in-progress | `in-progress` |
| ready-for-review | `ready-for-review` |
| in-review | `in-review` |
| grade-rejected | `grade-C` (or D/F) + `needs-revision` |
| merged | (GitHub auto, issue closed) |

---

## 16. Task Registry Schema v2.0

```json
{
  "schema_version": "2.0",
  "cycle": 7,
  "registry_hash": "<sha256>",
  "prev_registry_hash": "<sha256 of previous comment>",
  "updated_at": "2026-02-28T01:45:00Z",
  "updated_by": "merge-manager-7-a3f2",
  "tasks": [
    {
      "issue_number": 140,
      "phase": "merged",
      "branch": "feat/muse-harmony",
      "pull_request_number": 150,
      "grade": "A",
      "merged_at": "2026-02-28T01:44:00Z",
      "reject_reason": null,
      "attempt_counters": {
        "triage_attempts": 1,
        "impl_attempts": 1,
        "review_attempts": 1
      }
    }
  ]
}
```

**Chain invariants:**
- Every write includes the hash of the write it supersedes.
- A concurrent write is detected immediately (hash mismatch) and enters reconciliation.
- The Registry is the ground truth. GitHub labels are derived state — derived from the Registry, not the other way around.

---

## 17. Cross-Manager Sanity Checks

Managers post structured artifacts to `#COORD` after each phase. Peer managers
validate before consuming the output.

```
Triage Manager  ──artifact──▶  Build Manager validates input quality
Build Manager   ──artifact──▶  Merge Manager validates input quality
Merge Manager   ──artifact──▶  Triage Manager validates next-cycle scope
```

If validation fails: the downstream manager posts `ARTIFACT_VALIDATION_FAILED`
on `#COORD` (Severity S3), pauses its stage, and awaits Supervisor action.

### Coordination comment format

```
STAGE_ARTIFACT
stage: build
cycle: 7
manager_id: build-manager-7-a3f2
status: ok
input_items: [140, 141, 142, 143]
output_items: [150, 151, 152, 153]
artifact_hash: abc123...
produced_at: 2026-02-28T01:30:00Z
notes: "One conflict resolved in app.py (known-safe). All mypy clean."
```

---

## 18. Heartbeat / KPI Protocol

### Trigger

After every N merges (default: 5) the Merge Manager triggers a heartbeat.
The Supervisor assembles the full report using `rolling_metrics`.

### KPI thresholds

| KPI | GREEN | YELLOW | RED |
|-----|-------|--------|-----|
| Grade C/D/F rate (raw) | < 10% | 10–25% | > 25% |
| Grade C/D/F rate (trend) | flat | slope > 0.02 | — |
| Mypy clean rate | 100% | 95–99% | < 95% |
| Test pass rate | ≥ 99% | 95–98% | < 95% |
| review-to-merge (raw) | < 20 min | 20–45 min | > 45 min |
| review-to-merge (trend) | flat/down | slope > 1.0 | — |
| Loop-guard triggers | 0 | 1–2 | ≥ 3 |
| Orphaned worktrees | 0 | 1 | ≥ 2 |
| Resource denials (3× consecutive) | 0 | — | ≥ 1 |

GREEN = auto-continue. YELLOW = continue with flag. RED = pause + escalate.

### Heartbeat format

```markdown
## Heartbeat — Cycle 7 | <timestamp>

### KPIs
| Metric | This cycle | Rolling avg | Trend |
|--------|-----------|-------------|-------|
| Issues triaged | 6 | 4.2 | → |
| PRs merged | 4 | 3.6 | → |
| Grade A | 3 (75%) | 72% | → |
| Grade C/D/F | 0 | 4% | ↘ improving |
| Mypy clean | 100% | 98% | → |
| review→merge | 12 min | 14 min | ↘ improving |

### Status: 🟢 GREEN

### Slope analysis
  grade_c_rate: slope -0.003 ↘ [improving]
  review_to_merge_min: slope -0.5 ↘ [improving]

### Dev tip: abc1234 (+4 commits since last heartbeat)

Next cycle starts automatically.
```

### Human response protocol

- **GREEN:** no action needed. Machine continues.
- **YELLOW:** reply `ok continue` or `pause <stage>` on `#HEARTBEAT`.
- **RED:** machine is paused. Reply `resume` after investigating, or
  `close <issue-number>` to abandon a stuck task.

---

## 19. Permanent GitHub Issues (Create Once)

| Issue | Title | Purpose |
|-------|-------|---------|
| `#COORD` | 🤖 Agent Coordination Hub | Manager artifacts, escalations, peer validation |
| `#REGISTRY` | 🗂 Agent Task Registry | Hash-chained JSON state for all tasks |
| `#HEARTBEAT` | 💓 Agent Heartbeat & KPIs | Cycle KPIs, human approval, slope analysis |
| `#LEASE` | 🔐 Agent Lease Board | Manager lease tokens, TTL, conflict detection |

None of these are ever closed.

---

## 20. Replay Mode

```bash
# Replay cycle 7 — read-only, exits 0 if clean, 1 if drift
python -m agent_engine.replay --cycle 7

# Full audit with JSON output
python -m agent_engine.replay --cycle 7 --format json --output /tmp/replay-7.json
```

Replay reconstructs:
- The Registry snapshot for cycle N
- The actual GitHub state (labels, PR states, issue states)
- The full transition graph from Registry comment history
- A drift report: any discrepancy between Registry claim and GitHub reality

Replay is always safe to run. It never mutates state.

Run it:
- After any RED heartbeat before resuming
- After a Supervisor crash-recovery
- On demand as an audit tool
- As part of the Industrial Mode certification test

---

## 21. Industrial Mode Certification

The system is certified Industrial Mode when it completes 3 unattended
cycles with 20+ issues queued, satisfying all of the following:

| Check | Pass condition |
|-------|---------------|
| No duplicate PRs | `gh pr list --state all` shows no duplicate `closes #<N>` |
| No orphaned worktrees | `git worktree list` shows only main repo after each cycle |
| No registry drift | `python -m agent_engine.replay --cycle N` exits 0 for all N |
| No race conditions | Zero `RECONCILIATION_FAILED` or `LEASE_CONFLICT` events on `#COORD` |
| Stagnation detected | At least one intentional stall test caught by Supervisor |
| Performance stable | Rolling slope flat or improving for all KPIs |
| All merges A/B | Zero grade C/D/F merges (rejections re-queued, not forced) |
| Failure injection | All 5 failure injection tests pass |

Failure injection tests:
1. Kill a Build worker mid-implementation → orphaned worktree cleaned up, issue re-queued
2. Write a conflicting registry update simultaneously → reconciliation resolves cleanly
3. Expire a manager lease artificially → Supervisor detects and relaunches within 3 min
4. Push a mypy-failing PR → Review agent grades C, re-queues, does not merge
5. Trigger a resource guardrail (> 80% CPU) → Supervisor throttles dispatch, recovers

---

## 22. What Requires Human Judgment

The machine handles all mechanical work. Humans are needed for:

| Decision | Why human | How signaled |
|----------|-----------|-------------|
| Architecture changes | New frameworks, new layers | Grade D/F → Severity S3 escalation |
| API contract changes | Swift frontend must be notified | Review agent posts handoff → you action it |
| Security model changes | Cannot be delegated | Grade F → Severity S4, system halted |
| Persistent RED heartbeat | System degraded | `#HEARTBEAT` RED, notify human |
| `RECONCILIATION_FAILED` | Registry split-brain | Severity S4, human must resolve |
| Issue creation | Machine does not generate product ideas | You push; machine pulls |
| `force-reopen` label | Backward state transition needed | Human sets label + comments |

---

## 23. Deliverables Checklist

### Python modules (`agent_engine/`)

- [ ] `types.py` — `Stage`, `WorkflowState`, `TaskRecord`, `AttemptCounters`, `CycleMetrics`, `HealthStatus`
- [ ] `workflow_state_machine.py` — `WorkflowState`, `ALLOWED_TRANSITIONS`, `validate_transition`, `apply_transition`
- [ ] `agent_registry.py` — `compute_registry_hash`, `validate_registry_transition`, `load_latest_registry`, `write_registry_update`, `reconcile_registry`
- [ ] `lease_manager.py` — `acquire_lease`, `renew_lease`, `release_lease`, `detect_expired_leases`
- [ ] `stage_artifact.py` — `StageArtifact` Pydantic model, `validate_artifact`, `emit_artifact`
- [ ] `rolling_metrics.py` — `RollingMetrics`, `compute_trend`, `compute_health_status`
- [ ] `severity.py` — `Severity` enum, `SeverityEvent`, `emit_escalation`
- [ ] `concurrency.py` — `TokenBucket`, `DispatchDecision`, global limit constants
- [ ] `resource_monitor.py` — `check_docker_cpu_percent`, `check_docker_memory_mb`, `count_active_worktrees`, `check_dispatch_allowed`
- [ ] `replay.py` — `replay_cycle`, CLI entry point, drift report
- [ ] `supervisor.py` — `SupervisorLifecycle`, `monitor_leases`, `restart_manager`, `emit_system_heartbeat`

### Tests (`agent_engine/tests/`)

- [ ] `test_registry.py` — hash chain, concurrent write rejection, reconciliation
- [ ] `test_state_machine.py` — all valid transitions, all invalid transition rejections
- [ ] `test_metrics.py` — rolling window, trend computation, threshold evaluation
- [ ] `test_concurrency.py` — token bucket acquire/release, timeout
- [ ] `test_failure_injection.py` — all 5 failure scenarios from §21

### Scripts and config

- [ ] `agent_engine/supervisor_kickoff.sh` — pre-flight checks, launches Supervisor agent
- [ ] `agent_engine/requirements.txt` — pydantic, pytest (host-side deps only)
- [ ] `.env.agent_engine.example` — `AGENT_MAX_WORKERS_PER_STAGE`, `REGISTRY_ISSUE`, etc.

### Manager prompts (`.cursor/`)

- [ ] `MANAGER_TRIAGE.md` — full manager lifecycle for Stage 1
- [ ] `MANAGER_BUILD.md` — full manager lifecycle for Stage 2
- [ ] `MANAGER_MERGE.md` — full manager lifecycle for Stage 3

---

## 24. Known Constraints (Today's Tech)

| Constraint | Impact | Mitigation |
|-----------|--------|-----------|
| Cursor Task agents share the same model limits | Very large batches may hit context limits in manager agents | Keep batch ≤ 8/cycle; managers summarize, not replicate, worker output |
| No persistent agent memory across Cursor sessions | Managers must re-read GitHub state on every startup | Registry + Lease issues provide durable state |
| Docker concurrency | ≥ 12 simultaneous mypy/pytest may slow Docker | Token bucket caps total concurrent Docker execs |
| Cursor's 4-agent parallel Task limit | Can't dispatch > 4 workers simultaneously | Dispatch in waves of 4; collect before next wave |
| No native cron in Cursor | Managers must be launched manually | Supervisor auto-relaunches managers; human runs kickoff once |
| GitHub API rate limits (5000 req/hr) | Heavy polling can hit limits | `sleep 2` between `gh` calls; batch reads via GraphQL where possible |
| Lease TTL depends on wall-clock time | A stalled host clock breaks expiry detection | Use GitHub comment timestamp (server-side), not local clock |

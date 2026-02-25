# Muse VCS — Musical Version Control System

> **Status:** Canonical Implementation Reference
> **E2E demo:** [`muse_e2e_demo.md`](muse_e2e_demo.md)

---

## What Muse Is

Muse is a persistent, Git-style version control system for musical compositions. It tracks every committed change as a variation in a DAG (directed acyclic graph), enabling:

- **Commit history** — every accepted variation is recorded with parent lineage
- **Branching** — multiple variations can diverge from the same parent
- **Three-way merge** — auto-merges non-conflicting changes, reports conflicts
- **Drift detection** — compares HEAD snapshot against the live DAW state (`git status`)
- **Checkout / time travel** — reconstruct any historical state via deterministic tool calls
- **Log graph** — serialize the full commit DAG as Swift-ready JSON

---

## Module Map

```
app/services/
├── muse_repository.py        — Persistence adapter (DB reads/writes)
├── muse_replay.py            — History reconstruction (lineage walking)
├── muse_drift.py             — Drift detection engine (HEAD vs working)
├── muse_checkout.py          — Checkout plan builder (pure data → tool calls)
├── muse_checkout_executor.py — Checkout execution (applies plan to StateStore)
├── muse_merge_base.py        — Merge base finder (LCA in the DAG)
├── muse_merge.py             — Three-way merge engine
├── muse_history_controller.py— Orchestrates checkout + merge flows
├── muse_log_graph.py         — DAG serializer (topological sort → JSON)
├── muse_log_render.py        — ASCII graph + JSON + summary renderer
└── variation/
    └── note_matching.py      — Note + controller event matching/diffing

app/api/routes/
├── muse.py                   — Production HTTP routes (5 endpoints)
└── variation/                — Existing variation proposal routes

app/db/
└── muse_models.py            — ORM: Variation, Phrase, NoteChange tables

tests/
├── test_muse_persistence.py  — Repository + lineage tests
├── test_muse_drift.py        — Drift detection tests
├── test_muse_drift_controllers.py — Controller drift tests
├── test_commit_drift_safety.py    — 409 conflict enforcement
├── test_muse_checkout.py     — Checkout plan tests
├── test_muse_checkout_execution.py — Checkout execution tests
├── test_muse_merge.py        — Merge engine tests
├── test_muse_log_graph.py    — Log graph serialization tests
└── e2e/
    ├── muse_fixtures.py      — Deterministic IDs + snapshot builders
    └── test_muse_e2e_harness.py — Full VCS lifecycle E2E test
```

---

## Data Model

### Variation (ORM: `app/db/muse_models.py`)

| Column | Type | Purpose |
|--------|------|---------|
| `variation_id` | PK | Unique ID |
| `project_id` | FK | Project this belongs to |
| `parent_variation_id` | FK (self) | Primary parent (lineage) |
| `parent2_variation_id` | FK (self) | Second parent (merge commits only) |
| `is_head` | bool | Whether this is the current HEAD |
| `commit_state_id` | str | State version at commit time |
| `intent` | text | User intent / description |
| `status` | str | `ready` / `committed` / `discarded` |

### HeadSnapshot (`app/services/muse_replay.py`)

Reconstructed from walking the variation lineage. Contains the cumulative state at any point in history:

| Field | Type | Contents |
|-------|------|----------|
| `notes` | `dict[region_id, list[note_dict]]` | All notes per region |
| `cc` | `dict[region_id, list[cc_event]]` | CC events per region |
| `pitch_bends` | `dict[region_id, list[pb_event]]` | Pitch bends per region |
| `aftertouch` | `dict[region_id, list[at_event]]` | Aftertouch per region |
| `track_regions` | `dict[region_id, track_id]` | Region-to-track mapping |

---

## HTTP API

All routes require JWT auth (`Authorization: Bearer <token>`).
Prefix: `/api/v1/muse/`

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/muse/variations` | Save a variation directly into history |
| `POST` | `/muse/head` | Set HEAD pointer to a variation |
| `GET` | `/muse/log?project_id=X` | Get the full commit DAG as `MuseLogGraph` JSON |
| `POST` | `/muse/checkout` | Checkout to a variation (time travel) |
| `POST` | `/muse/merge` | Three-way merge of two variations |

### Response codes

| Code | Meaning |
|------|---------|
| 200 | Success |
| 404 | Variation not found (checkout) |
| 409 | Checkout blocked by drift / merge has conflicts |

---

## VCS Primitives

### Commit (save + set HEAD)

```
save_variation(session, variation, project_id, parent_variation_id, ...)
set_head(session, variation_id)
```

### Lineage

```
get_lineage(session, variation_id) → [root, ..., target]
get_head(session, project_id) → HistoryNode | None
get_children(session, variation_id) → [HistoryNode, ...]
```

### Drift Detection

```
compute_drift_report(head_snapshot, working_snapshot, ...) → DriftReport
```

Compares HEAD (from DB) against working state (from StateStore). Severity levels: `CLEAN`, `DIRTY`, `DIVERGED`.

### Replay / Reconstruction

```
reconstruct_head_snapshot(session, project_id) → HeadSnapshot
reconstruct_variation_snapshot(session, variation_id) → HeadSnapshot
build_replay_plan(session, project_id, target_id) → ReplayPlan
```

### Checkout

```
build_checkout_plan(target_notes, working_notes, ...) → CheckoutPlan
execute_checkout_plan(plan, store, trace) → CheckoutExecutionResult
checkout_to_variation(session, project_id, target_id, store, ...) → CheckoutSummary
```

### Merge

```
find_merge_base(session, a, b) → str | None
build_merge_result(base, left, right) → MergeResult
merge_variations(session, project_id, left, right, store, ...) → MergeSummary
```

### Log Graph

```
build_muse_log_graph(session, project_id) → MuseLogGraph
```

Topologically sorted (Kahn's algorithm), deterministic tie-breaking by `(timestamp, variation_id)`. Output is camelCase JSON for the Swift frontend.

---

## Architectural Boundaries

17 AST-enforced rules in `scripts/check_boundaries.py`. Key constraints:

| Module | Must NOT import |
|--------|----------------|
| `muse_repository` | StateStore, executor, VariationService |
| `muse_replay` | StateStore, executor, LLM handlers |
| `muse_drift` | StateStore, executor, LLM handlers |
| `muse_checkout` | StateStore, executor, handlers |
| `muse_checkout_executor` | LLM handlers, VariationService |
| `muse_merge`, `muse_merge_base` | StateStore, executor, MCP, handlers |
| `muse_log_graph` | StateStore, executor, handlers, engines |
| `note_matching` | handlers, StateStore |

The boundary philosophy: Muse VCS modules are **pure data** — they consume snapshots and produce plans/reports. StateStore mutation only happens in `muse_checkout_executor` (via duck-typed store parameter) and the history controller.

---

## E2E Demo

Run the full VCS lifecycle test:

```bash
docker compose exec maestro pytest tests/e2e/test_muse_e2e_harness.py -v -s
```

Exercises: commit → branch → merge → conflict detection → checkout traversal.
Produces: ASCII graph, JSON dump, summary table. See `muse_e2e_demo.md` for details.

---

## Evolution History

| Phase | Feature | Key modules |
|-------|---------|-------------|
| 5 | Lineage + replay engine | `muse_repository`, `muse_replay` |
| 6 | Drift detection | `muse_drift` |
| 7 | Controller persistence (CC, PB, AT) | `note_matching`, `muse_drift` |
| 8 | Drift-aware commit safety (409) | `commit.py` |
| 9 | Checkout engine | `muse_checkout` |
| 10 | Checkout execution | `muse_checkout_executor`, `muse_history_controller` |
| 12 | Merge engine | `muse_merge`, `muse_merge_base`, `muse_history_controller` |
| 13 | Log graph serialization | `muse_log_graph` |
| E2E | Tour de force harness | `muse.py` (routes), `muse_log_render`, E2E test |

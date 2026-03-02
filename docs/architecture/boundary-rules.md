# Architectural Boundary Rules

Enforced by `scripts/check_boundaries.py` (8 rules). Run locally or in CI.

---

## Import Rules

### Allowed imports

| Source | May import |
|--------|-----------|
| API Routes | Core, Models, Variation storage, Services, MuseRepository |
| Maestro Orchestration (`maestro_handlers`) | Editing, Composing, Agent Teams, StateStore, LLM |
| Maestro Composing (`maestro_composing/`) | Executor, VariationService, StateStore, Snapshots, Models |
| Executor (`executor/`) | StateStore, Models, Services, Tracing, ToolName |
| Muse Compute (`compute_variation_from_context`) | VariationService, Models — nothing else |
| VariationService (`services/variation/`) | Models (`maestro.models.variation`) — nothing else |
| MuseRepository (`services/muse_repository.py`) | DB models (`maestro.db.muse_models`), domain models (`maestro.models.variation`) — nothing else |
| StateStore | EntityRegistry |

### Forbidden imports

| Source | Must NOT import | Reason |
|--------|----------------|--------|
| `app/services/variation/**` | `maestro.core.state_store`, `maestro.core.entity_registry` | VariationService is a pure computation service |
| `compute_variation_from_context` | `StateStore`, `EntityRegistry`, executor modules | Muse compute is a pure function of data |
| `apply_variation_phrases` | `get_or_create_store` | Receives store as explicit param |
| `maestro_handlers.py` | `maestro.models.variation`, `maestro.services.variation` | Must not import Muse models directly |
| `muse_repository.py` | `maestro.core.state_store`, `maestro.core.executor` | Persistence adapter must not couple to execution layer |
| `VariationContext` | `StateStore` (as field) | Data-only container; store access via `VariationExecutionContext` |

---

## Snapshot Boundary Rules

1. All snapshot data flows through `SnapshotBundle` — one type, one shape, everywhere.
2. `VariationContext` stores `base: SnapshotBundle` and `proposed: SnapshotBundle` — no store reference.
3. `VariationExecutionContext` holds the store — lives only inside the executor, never crosses the Muse boundary.
4. `compute_variation_from_context` uses **keyword-only** arguments — structurally prevents passing a store.
5. `apply_variation_phrases` receives `region_metadata` as a parameter — never reads `store.registry`.
6. `_store_variation` receives `base_state_id`, `conversation_id`, and `region_metadata` as params — never reads StateStore.
7. Snapshots are captured via `capture_base_snapshot` / `capture_proposed_snapshot` (returns `SnapshotBundle`).

---

## StateStore Rules

**StateStore IS:** a per-session mutable working tree, a scratchpad for tool execution, a source of snapshots.

**StateStore IS NOT:** a persistent store, a Muse repository, an authority on musical history.

**StateStore MAY:** maintain tracks/regions/notes/buses, accept mutations, resolve entity names, provide versioned state, support transactions, sync from DAW.

**StateStore MUST NOT:** be accessed directly by Muse commit logic, store variation/phrase data, be shared across requests for Muse's benefit, appear as a field on `VariationContext`.

---

## Persistence Rules

1. `_store_variation` performs a **dual write** — in-memory `VariationStore` + Postgres via `muse_repository`.
2. The commit path reads from **Postgres first**, falling back to in-memory for pre-persistence variations.
3. `muse_repository` is the **only** module that touches the `variations`/`phrases`/`note_changes` tables.

---

## Verification

```bash
python scripts/check_boundaries.py
```

Or inside Docker:

```bash
docker compose exec maestro python scripts/check_boundaries.py
```

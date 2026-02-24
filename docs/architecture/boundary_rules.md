# Architectural Boundary Rules

Enforced by `scripts/check_boundaries.py`. Run locally or in CI.

---

## Import Rules

### Allowed imports

| Source | May import |
|--------|-----------|
| API Routes | Core, Models, Variation storage, Services |
| Maestro Orchestration (`maestro_handlers`) | Editing, Composing, Agent Teams, StateStore, LLM |
| Maestro Composing (`maestro_composing/`) | Executor, VariationService, StateStore, Snapshots, Models |
| Executor (`executor/`) | StateStore, Models, Services, Tracing |
| Muse Compute (`compute_variation_from_context`) | VariationService, Models — nothing else |
| VariationService (`services/variation/`) | Models (`app.models.variation`) — nothing else |
| StateStore | EntityRegistry |

### Forbidden imports

| Source | Must NOT import | Reason |
|--------|----------------|--------|
| `app/services/variation/**` | `app.core.state_store`, `app.core.entity_registry` | VariationService is a pure computation service |
| `compute_variation_from_context` | `StateStore`, `EntityRegistry` | Muse compute is a pure function of data |
| `apply_variation_phrases` | `get_or_create_store` | Receives store as explicit param |
| `maestro_handlers.py` | `app.models.variation`, `app.services.variation` | Must not import Muse models directly |

---

## Snapshot Boundary Rules

1. Muse computation receives only **frozen data** — plain dicts of notes, CC, pitch bends, aftertouch.
2. `compute_variation_from_context` uses **keyword-only** arguments — structurally prevents passing a store.
3. `apply_variation_phrases` receives `region_metadata` as a parameter — never reads `store.registry`.
4. `_store_variation` receives `base_state_id`, `conversation_id`, and `region_metadata` as params — never reads StateStore.
5. Snapshots are captured via `capture_base_snapshot` / `capture_proposed_snapshot` (agent-team path) or via `VariationContext` incremental capture (single-instrument path).

---

## StateStore Rules

**StateStore IS:** a per-session mutable working tree, a scratchpad for tool execution, a source of snapshots.

**StateStore IS NOT:** a persistent store, a Muse repository, an authority on musical history.

**StateStore MAY:** maintain tracks/regions/notes/buses, accept mutations, resolve entity names, provide versioned state, support transactions, sync from DAW.

**StateStore MUST NOT:** be accessed directly by Muse commit logic, store variation/phrase data, be shared across requests for Muse's benefit.

---

## Verification

```bash
python scripts/check_boundaries.py
```

Or inside Docker:

```bash
docker compose exec maestro python scripts/check_boundaries.py
```

# Tour de Force Scenarios

This document describes the branching/merge workflows and MUSE VCS permutations exercised by the Tour de Force harness.

## MUSE Permutation Coverage

Every scenario exercises a subset of MUSE primitives. Across all scenarios, the harness covers **every** MUSE operation:

| Operation | Scenario(s) | Description |
|-----------|------------|-------------|
| **commit** | All | `save_variation` + `set_head` |
| **branch** | All except minimal | `save_variation` with `parent_variation_id` |
| **clean merge** | Standard, Checkout Stress | Three-way merge with disjoint regions (auto-resolve) |
| **conflict merge** | Standard, Conflict Only | Three-way merge where both branches modify same region — returns 409 |
| **checkout (force)** | Standard, Checkout Stress, Conflict Only | Force checkout across DAG (time travel) |
| **checkout (non-force)** | Checkout Stress | Non-force checkout — may be blocked by drift (409) |
| **drift detection** | Standard, Checkout Stress, Conflict Only | Attempt non-force checkout after state mutation |
| **force recovery** | Standard, Checkout Stress, Conflict Only | Force-checkout after drift block to recover |
| **graph export** | All | Log DAG → ASCII + JSON |
| **lineage traversal** | All (via graph) | Parent chain walking in topological order |

## Scenario 1: Standard (full lifecycle + conflict + checkout)

**Name:** `compose->commit->edit->branch->merge->conflict->checkout`

### Flow

```
              C1 (compose)
             / \
            /   \
    C2 (bass)   C3 (drums)        ← disjoint regions
            \   /
             \ /
          M1 (clean merge)
             / \
            /   \
   C4 (keys)   C5 (arrangement)   ← disjoint regions
            \   /
             \ /
          M2 (clean merge)
             / \
            /   \
  C6 (keys    C7 (keys             ← SAME region (deliberate conflict)
   rewrite A)   rewrite B)
            \   /
             \ /
        CONFLICT (409)

  Then: checkout traversal C1 → M1 → M2 → C1
  Then: drift detection test
```

### Steps

1. **Compose (C1)**: Fetch prompt → Maestro COMPOSING → tracks/regions/MIDI via Orpheus.
2. **Wave 1 — Edit Branches (disjoint)**:
   - `bass_tighten (C2)`: Edit bass track only, branch from C1.
   - `drums_variation (C3)`: Edit drums track only, branch from C1.
3. **Wave 1 — Clean Merge (M1)**: Three-way merge of C2 + C3. Disjoint regions → auto-resolve.
4. **Wave 2 — Edit Branches (disjoint)**:
   - `keys_reharm (C4)`: Edit keys track only, branch from M1.
   - `arrangement_extend (C5)`: Edit arrangement only, branch from M1.
5. **Wave 2 — Clean Merge (M2)**: Three-way merge of C4 + C5. Disjoint → auto-resolve.
6. **Deliberate Conflict (C6 vs C7)**:
   - `keys_rewrite_a (C6)`: Completely rewrite keys with descending chromatic line, branch from M2.
   - `keys_rewrite_b (C7)`: Completely rewrite keys with ascending whole-tone scale, branch from M2.
   - **Merge attempt**: Both branches modify the same keys region → MUSE returns 409 with conflict details.
   - Conflict details recorded as artifact.
7. **Checkout Traversal**: Force-checkout through C1 → M1 → M2 → C1 (time travel).
8. **Drift Detection**: Non-force checkout attempt → observe drift block → force recovery.

## Scenario 2: Conflict Only

**Name:** `compose->conflict->recover`

### Flow

```
    C1 (compose)
     / \
    /   \
  C2     C3        ← same keys region, different content
    \   /
     \ /
  CONFLICT (409)
    |
  checkout C1 (recovery)
```

Focused on exercising merge conflict detection and recovery. No clean merges.

## Scenario 3: Checkout Stress

**Name:** `compose->branch->checkout-stress`

### Flow

```
    C1 (compose)
     / \
    /   \
  C2     C3        ← disjoint (simplify vs accent)
    \   /
     \ /
  M1 (clean merge)

  Then: rapid checkout traversal:
    C1 → C2 → C3 → M1 → C1 (non-force) → C1 (force)
  Then: drift detection test
```

Focused on exercising checkout traversal across the DAG and drift detection.

## Scenario 4: Minimal

**Name:** `compose->commit->edit`

### Flow

```
    C1 (compose)
      |
    C2 (humanize)
```

Single edit branch, no merge. Exercises the simplest VCS path.

## Edit Prompt Catalog

| Key | Target Track | Description |
|-----|-------------|-------------|
| `bass_tighten` | bass | Tighten groove, add syncopation |
| `drums_variation` | drums | Ghost notes, ride cymbal, hi-hat variation |
| `keys_reharm` | keys | Reharmonize with extended chords |
| `arrangement_extend` | arrangement | Add B section, 4-bar transition fill |
| `keys_rewrite_a` | keys | Descending chromatic, staccato (conflict branch A) |
| `keys_rewrite_b` | keys | Ascending whole-tone, legato (conflict branch B) |
| `humanize` | all | Swing, micro-timing, velocity variation |
| `simplify` | all | Remove unnecessary notes, create space |
| `accent_dynamics` | all | Crescendos, decrescendos across phrase |
| `add_fills` | drums | Drum fills at phrase boundaries |

## Merge Conflict Mechanics

MUSE detects conflicts when both branches:
- Add notes at the **same (pitch, start_beat)** position but with **different content** (velocity, duration)
- Modify the **same existing note** differently
- One removes a note the other modifies

Conflict details include:
- `region_id` — which region conflicted
- `type` — `note`, `cc`, `pb`, `at`
- `description` — human-readable explanation

When a conflict occurs, the harness:
1. Records full conflict details in `muse/run_<id>/merge_conflicts.json`
2. Emits a structured event with `event_type=error`, `tags.error_type=merge_conflict`
3. Continues to the next step (does not abort the run)

## Success Criteria

A run is "successful" only if ALL of:

- Prompt fetched with JWT auth
- Maestro compose stream completes (state=composing, success=true)
- MIDI data produced (note_count > 0)
- MIDI passes sanity checks (quality_score > 0)
- MUSE commit created for compose (C1)
- At least one edit branch produces a commit
- Clean merges succeed where expected (disjoint regions)
- Conflict merges return 409 where expected (overlapping regions)
- Checkout traversal completes without errors
- All artifacts and JSONL events are present
- Report generation completes

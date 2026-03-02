# Muse E2E Demo — Tour de Force

## What it proves

The E2E harness exercises every Muse VCS primitive through real HTTP routes
and a real database in a single deterministic scenario:

| Step | Operation | Asserts |
|------|-----------|---------|
| 0 | Root commit (C0) | Graph has 1 node, HEAD correct |
| 1 | Mainline commit (C1 — keys) | Checkout executes, HEAD moves |
| 2 | Branch A (C2 — bass) | Graph shows branch from C1 |
| 3 | Branch B (C3 — drums) | Time-travel back to C1, then branch |
| 4 | Merge C2 + C3 → C4 | Auto-merge, two parents, HEAD moves |
| 5 | Conflict merge (C5 vs C6) | 409 with conflict payload |
| 7 | Checkout traversal | C1 → C2 → C4, all transactional |

## How to run

```bash
docker compose exec maestro pytest tests/e2e/test_muse_e2e_harness.py -v -s
```

The `-s` flag is important — it shows the ASCII graph, JSON dump, and
summary table in the terminal.

## Expected output

```
═══ Step 0: Initialize ═══
  ✅ Root C0 committed, HEAD=c0000000

═══ Step 1: Mainline commit C1 (keys v1) ═══
  ✅ C1 committed + checked out, executed=2 tool calls

═══ Step 2: Branch A — bass v1 (C2) ═══
  ✅ C2 committed, HEAD=c2000000, graph has 3 nodes

═══ Step 3: Branch B — drums v1 (C3) ═══
  ✅ C3 committed, HEAD=c3000000

═══ Step 4: Merge C2 + C3 ═══
  ✅ Merge commit C4=<uuid>, executed=N tool calls
  ✅ Merge commit has parent=..., parent2=...

═══ Step 5: Conflict merge demo (C5 vs C6) ═══
  ✅ Conflict detected: N conflict(s)
     note: Both sides added conflicting note at pitch=... beat=...

═══ Step 7: Checkout traversal ═══
  → Checked out C1: executed=N, hash=...
  → Checked out C2: executed=N, hash=...
  → Checked out C4 (merge): executed=N, hash=...
  ✅ All checkouts transactional

════════════════════════════════════════════════════════════
  MUSE LOG GRAPH — ASCII
════════════════════════════════════════════════════════════
* c4_merge merge (HEAD)
| \
| * c3000000 drums v1
* | c2000000 bass v1
|/
* c1000000 keys v1
* c0000000 root

════════════════════════════════════════════════════════════
  SUMMARY
════════════════════════════════════════════════════════════
┌────────────────────────────────┬──────┐
│ Commits                        │    7 │
│ Merges                         │    1 │
│ Branch heads                   │    3 │
│ Conflict merges attempted      │    1 │
│ Checkouts executed             │    7 │
│ Drift blocks                   │    0 │
│ Forced operations              │    7 │
└────────────────────────────────┴──────┘
```

## Architecture

```
tests/e2e/
├── __init__.py
├── muse_fixtures.py          # Deterministic IDs, snapshots, payload builder
└── test_muse_e2e_harness.py  # The scenario + assertions

app/api/routes/muse.py        # Production routes (variations, head, log, checkout, merge)
app/services/muse_log_render.py  # ASCII graph + JSON + summary renderer
```

All routes are production-grade with JWT auth. The test uses the standard
`auth_headers` fixture from `tests/conftest.py`.

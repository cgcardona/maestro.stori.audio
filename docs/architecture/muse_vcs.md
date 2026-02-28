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

## Why Muse and not Git?

> *"Can't we just commit MIDI files to Git?"*

You can. And you'll immediately discover everything Git cannot tell you about music.

### The core problem: Git sees music as bytes, not music

`git diff` on a MIDI file produces binary noise. `git log` tells you "file changed." That's it. Git is a filesystem historian — it records *which bytes* changed, not *what happened musically*.

Music is **multidimensional** and **happens in time**. A single session commit might simultaneously change the key, the groove, the instrumentation, the dynamic arc, and the emotional character — dimensions that share zero representation in Git's diff model.

### What Muse can do that Git categorically cannot

| Question | Git | Muse |
|----------|-----|------|
| What key is this arrangement in? | ❌ | ✅ `muse key HEAD` |
| How did the chord progression change between commit 12 and commit 47? | ❌ | ✅ `muse diff HEAD~35 HEAD --harmonic` |
| When did the song modulate from Eb major to F minor? | ❌ | ✅ `muse find --harmony "key=F minor"` |
| Did the groove get tighter or looser over 200 commits? | ❌ | ✅ `muse groove-check HEAD~200 HEAD` |
| Find me all versions where the chorus had a string layer | ❌ | ✅ `muse find --structure "has=strings" --structure "section=chorus"` |
| Where does the main motif first appear, and how was it transformed? | ❌ | ✅ `muse motif track "main-theme"` |
| What was the arrangement before we cut the bridge? | ❌ | ✅ `muse arrange HEAD~10` |
| How musically similar are these two alternative mixes? | ❌ | ✅ `muse similarity mix-a mix-b` |
| "Find a melancholic minor-key version with sparse texture" | ❌ | ✅ `muse recall "melancholic minor sparse"` |
| What is the full musical state of this project for AI generation? | ❌ | ✅ `muse context --json` |

### Music is multidimensional — diffs should be too

When a producer changes a session, five things may change at once:

- **Harmonic** — a new chord substitution shifts the tension profile
- **Rhythmic** — the drummer's part gets slightly more swing
- **Structural** — a breakdown section is added before the final chorus
- **Dynamic** — the overall level is pushed 6dB louder in the chorus
- **Melodic** — the piano melody gets a new phrase in bar 7

Git records all of this as: *"beat.mid changed."*

Muse records all of this as five orthogonal dimensions, each independently queryable, diffable, and searchable across the full commit history.

### Muse as AI musical memory

This is where the difference is sharpest. An AI agent generating music needs to answer:

- What key are we in right now?
- What's the established chord progression?
- Which sections already have strings? Which don't?
- Has the energy been building or falling across the last 10 commits?
- What emotional arc are we maintaining?

`muse context --json` answers all of this in one call — a structured document containing the key, tempo, mode, chord progression, arrangement matrix, dynamic arc, emotional state, and 10-commit evolutionary history. An agent with this context makes musically coherent decisions. An agent without it is generating blind.

Git provides zero of this. Muse was built because musical creativity is multidimensional, happens in time, and deserves version control that understands music — not just files.

---

## Module Map

### CLI Entry Point

```
maestro/muse_cli/
├── __init__.py          — Package marker
├── app.py               — Typer application root (console script: `muse`)
├── errors.py            — Exit-code enum (0 success / 1 user / 2 repo / 3 internal) + exceptions
│                          MuseNotARepoError = RepoNotFoundError (public alias, issue #46)
├── _repo.py             — Repository detection (.muse/ directory walker)
│                          find_repo_root(), require_repo(), require_repo_root alias
├── repo.py              — Public re-export of _repo.py (canonical import surface, issue #46)
└── commands/
    ├── __init__.py
    ├── init.py           — muse init  ✅ fully implemented
    ├── status.py         — muse status  ✅ fully implemented (issue #44)
    ├── commit.py         — muse commit  ✅ fully implemented (issue #32)
    ├── log.py            — muse log    ✅ fully implemented (issue #33)
    ├── snapshot.py       — walk_workdir, hash_file, build_snapshot_manifest, compute IDs,
    │                        diff_workdir_vs_snapshot (added/modified/deleted/untracked sets)
    ├── models.py         — MuseCliCommit, MuseCliSnapshot, MuseCliObject (SQLAlchemy)
    ├── db.py             — open_session, upsert/get helpers, get_head_snapshot_manifest
    ├── merge_engine.py   — find_merge_base(), diff_snapshots(), detect_conflicts(),
    │                        apply_merge(), read/write_merge_state(), MergeState dataclass
    ├── checkout.py       — muse checkout (stub — issue #34)
    ├── merge.py          — muse merge   ✅ fast-forward + 3-way merge (issue #35)
    ├── remote.py         — muse remote  (stub — issue #38)
    ├── push.py           — muse push    (stub — issue #38)
    ├── pull.py           — muse pull    (stub — issue #38)
    ├── open_cmd.py       — muse open    ✅ macOS artifact preview (issue #45)
    └── play.py           — muse play    ✅ macOS audio playback via afplay (issue #45)
```

`maestro/muse_cli/artifact_resolver.py` — `resolve_artifact_async()` / `resolve_artifact()`:
resolves a user-supplied path-or-commit-ID to a concrete `pathlib.Path` (see below).

The CLI delegates to existing `maestro/services/muse_*.py` service modules. Stub subcommands print "not yet implemented" and exit 0.

---

## `muse merge` — Fast-Forward and 3-Way Merge

`muse merge <branch>` integrates another branch into the current branch.

### Algorithm

1. **Guard** — If `.muse/MERGE_STATE.json` exists, a merge is already in progress. Exit 1 with: *"Merge in progress. Resolve conflicts and run `muse merge --continue`."*
2. **Resolve commits** — Read HEAD commit ID for the current branch and the target branch from their `.muse/refs/heads/<branch>` ref files.
3. **Find merge base** — BFS over the commit graph to find the LCA (Lowest Common Ancestor) of the two HEAD commits. Both `parent_commit_id` and `parent2_commit_id` are traversed (supporting existing merge commits).
4. **Fast-forward** — If `base == ours`, the target is strictly ahead of current HEAD. Move the current branch pointer to `theirs` without creating a new commit.
5. **Already up-to-date** — If `base == theirs`, current branch is already ahead. Exit 0.
6. **3-way merge** — When branches have diverged:
   - Compute `diff(base → ours)` and `diff(base → theirs)` at file-path granularity.
   - Detect conflicts: paths changed on *both* sides since the base.
   - If **no conflicts**: auto-merge (take the changed side for each path), create a merge commit with two parent IDs, advance the branch pointer.
   - If **conflicts**: write `.muse/MERGE_STATE.json` and exit 1 with a conflict summary.

### `MERGE_STATE.json` Schema

Written on conflict, read by `muse status` and `muse commit` to block further operations:

```json
{
    "base_commit":    "abc123...",
    "ours_commit":    "def456...",
    "theirs_commit":  "789abc...",
    "conflict_paths": ["beat.mid", "lead.mp3"],
    "other_branch":   "feature/experiment"
}
```

All fields except `other_branch` are required. `conflict_paths` is sorted alphabetically.

### Merge Commit

A successful 3-way merge creates a commit with:
- `parent_commit_id` = `ours_commit_id` (current branch HEAD at merge time)
- `parent2_commit_id` = `theirs_commit_id` (target branch HEAD)
- `snapshot_id` = merged manifest (non-conflicting changes from both sides)
- `message` = `"Merge branch '<branch>' into <current_branch>"`

### Path-Level Granularity (MVP)

This merge implementation operates at **file-path level**. Two commits that modify the same file path (even if the changes are disjoint within the file) are treated as a conflict. Note-level merging (music-aware diffs inside MIDI files) is a future enhancement reserved for the existing `maestro/services/muse_merge.py` engine.

---

## Artifact Resolution (`artifact_resolver.py`)

`resolve_artifact_async(path_or_commit_id, root, session)` resolves a user-supplied
string to a concrete `pathlib.Path` in this priority order:

1. **Direct filesystem path** — if the argument exists on disk, return it as-is.
   No DB query is needed.
2. **Relative to `muse-work/`** — if `<root>/muse-work/<arg>` exists, return that.
3. **Commit-ID prefix** — if the argument is 4–64 lowercase hex characters:
   - Query `muse_cli_commits` for commits whose `commit_id` starts with the prefix.
   - If exactly one match: load its `MuseCliSnapshot` manifest.
   - If the snapshot has one file: resolve `<root>/muse-work/<file>`.
   - If the snapshot has multiple files: prompt the user to select one interactively.
   - Exit 1 if the prefix is ambiguous (> 1 commit) or the file no longer exists
     in the working tree.

### Why files must still exist in `muse-work/`

Muse stores **metadata** (file paths → sha256 hashes) in Postgres, not the raw
bytes. The actual content lives only on the local filesystem in `muse-work/`.
If a user deletes or overwrites a file after committing, the snapshot manifest
knows what _was_ there but the bytes are gone. `muse open` / `muse play` will
exit 1 with a clear error in that case.

---

## `muse status` Output Formats

`muse status` operates in three modes depending on repository state.

### Mode 1 — Clean working tree

No changes since the last commit:

```
On branch main
nothing to commit, working tree clean
```

### Mode 2 — Uncommitted changes

Files have been modified, added, or deleted relative to the last snapshot:

```
On branch main

Changes since last commit:
  (use "muse commit -m <msg>" to record changes)

        modified:   beat.mid
        new file:   lead.mp3
        deleted:    scratch.mid
```

- `modified:` — file exists in both the last snapshot and `muse-work/` but its sha256 hash differs.
- `new file:` — file is present in `muse-work/` but absent from the last committed snapshot.
- `deleted:` — file was in the last committed snapshot but is no longer present in `muse-work/`.

### Mode 3 — In-progress merge

When `.muse/MERGE_STATE.json` exists (written by `muse merge` when conflicts are detected):

```
On branch main

You have unmerged paths.
  (fix conflicts and run "muse commit")

Unmerged paths:
        both modified:   beat.mid
        both modified:   lead.mp3
```

Resolve conflicts manually, then `muse commit` to record the merge.

### No commits yet

On a branch that has never been committed to:

```
On branch main, no commits yet

Untracked files:
  (use "muse commit -m <msg>" to record changes)

        beat.mid
```

If `muse-work/` is empty or missing: `On branch main, no commits yet` (single line).

### Implementation

| Layer | File | Responsibility |
|-------|------|----------------|
| Command | `maestro/muse_cli/commands/status.py` | Typer callback + `_status_async` |
| Diff engine | `maestro/muse_cli/snapshot.py` | `diff_workdir_vs_snapshot()` |
| Merge reader | `maestro/muse_cli/merge_engine.py` | `read_merge_state()` / `MergeState` |
| DB helper | `maestro/muse_cli/db.py` | `get_head_snapshot_manifest()` |

`_status_async` is the injectable async core (tested directly without a running server).
Exit codes: 0 success, 2 outside a Muse repo, 3 internal error.

---

## `muse log` Output Formats

### Default (`git log` style)

```
commit a1b2c3d4e5f6...  (HEAD -> main)
Parent: f9e8d7c6
Date:   2026-02-27 17:30:00

    boom bap demo take 1

commit f9e8d7c6...
Date:   2026-02-27 17:00:00

    initial take
```

Commits are printed newest-first.  The first commit (root) has no `Parent:` line.

### `--graph` mode

Reuses `maestro.services.muse_log_render.render_ascii_graph` by adapting `MuseCliCommit` rows to the `MuseLogGraph`/`MuseLogNode` dataclasses the renderer expects.

```
* a1b2c3d4 boom bap demo take 1 (HEAD)
* f9e8d7c6 initial take
```

Merge commits (two parents) require `muse merge` (issue #35) — `parent2_commit_id` is reserved for that iteration.

### Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--limit N` / `-n N` | 1000 | Cap the walk at N commits |
| `--graph` | off | ASCII DAG mode |

---

## Commit Data Model

`muse commit` persists three content-addressed table types to Postgres:

### `muse_cli_objects` — File blobs (sha256-keyed)

| Column | Type | Description |
|--------|------|-------------|
| `object_id` | `String(64)` PK | `sha256(file_bytes)` hex digest |
| `size_bytes` | `Integer` | Raw file size |
| `created_at` | `DateTime(tz=True)` | Wall-clock insert time |

Objects are deduplicated across commits: the same file committed on two branches is stored exactly once.

### `muse_cli_snapshots` — Snapshot manifests

| Column | Type | Description |
|--------|------|-------------|
| `snapshot_id` | `String(64)` PK | `sha256(sorted("path:object_id" pairs))` |
| `manifest` | `JSON` | `{rel_path: object_id}` mapping |
| `created_at` | `DateTime(tz=True)` | Wall-clock insert time |

Two identical working trees always produce the same `snapshot_id`.

### `muse_cli_commits` — Commit history

| Column | Type | Description |
|--------|------|-------------|
| `commit_id` | `String(64)` PK | Deterministic sha256 (see below) |
| `repo_id` | `String(36)` | UUID from `.muse/repo.json` |
| `branch` | `String(255)` | Branch name at commit time |
| `parent_commit_id` | `String(64)` nullable | Previous HEAD commit on branch |
| `snapshot_id` | `String(64)` FK | Points to the snapshot row |
| `message` | `Text` | User-supplied commit message |
| `author` | `String(255)` | Reserved (empty for MVP) |
| `committed_at` | `DateTime(tz=True)` | Timestamp used in hash derivation |
| `created_at` | `DateTime(tz=True)` | Wall-clock DB insert time |

### ID Derivation (deterministic)

```
object_id   = sha256(file_bytes)
snapshot_id = sha256("|".join(sorted(f"{path}:{oid}" for path, oid in manifest.items())))
commit_id   = sha256(
                "|".join(sorted(parent_ids))
                + "|" + snapshot_id
                + "|" + message
                + "|" + committed_at.isoformat()
              )
```

Given the same working tree state, message, and timestamp two machines produce identical IDs. `sorted()` ensures insertion-order independence for both snapshot manifests and parent lists.

---

## Local Repository Structure (`.muse/`)

`muse init` creates the following layout in the current working directory:

```
.muse/
  repo.json          Repo identity: repo_id (UUID), schema_version, created_at
  HEAD               Current branch pointer, e.g. "refs/heads/main"
  config.toml        [user], [auth], [remotes] configuration
  refs/
    heads/
      main           Commit ID of branch HEAD (empty = no commits yet)
      <branch>       One file per branch
```

### File semantics

| File | Source of truth for | Notes |
|------|-------------------|-------|
| `repo.json` | Repo identity | `repo_id` persists across `--force` reinitialise |
| `HEAD` | Current branch name | Always `refs/heads/<branch>` |
| `refs/heads/<branch>` | Branch → commit pointer | Empty string = branch has no commits yet |
| `config.toml` | User identity, auth token, remotes | Not overwritten on `--force` |

### Repo-root detection

Every CLI command locates the active repo by walking up the directory tree until `.muse/` is found:

```python
# Public API — maestro/muse_cli/repo.py (issue #46)
from maestro.muse_cli.repo import find_repo_root, require_repo_root

root: Path | None = find_repo_root()          # returns None if not found, never raises
root: Path        = require_repo_root()        # exits 2 with git-style error if not found
```

Detection rules (in priority order):

1. If `MUSE_REPO_ROOT` env var is set, use it (useful in tests and scripts — no traversal).
2. Walk from `start` (default `Path.cwd()`) upward until a directory containing `.muse/` is found.
3. If the filesystem root is reached with no `.muse/`, return `None`.

`require_repo_root()` exits 2 with:
```
fatal: not a muse repository (or any parent up to mount point /)
Run "muse init" to initialize a new repository.
```

**Import path:** prefer the public `maestro.muse_cli.repo` module for new code; existing commands use `maestro.muse_cli._repo` which is kept for compatibility. Both expose the same functions. `MuseNotARepoError` in `errors.py` is the canonical alias for `RepoNotFoundError`.

### `config.toml` example

```toml
[user]
name = "Gabriel"
email = "g@example.com"

[auth]
token = "eyJ..."     # Muse Hub Bearer token — keep out of version control

[remotes]
[remotes.origin]
url = "https://story.audio/musehub/repos/abcd1234"
```

> **Security note:** `.muse/config.toml` contains the Hub auth token. Add `.muse/config.toml` to `.gitignore` (or `.museignore`) to prevent accidental exposure.

### VCS Services

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

## Muse Hub — Remote Backend

The Muse Hub is a lightweight GitHub-equivalent that lives inside the Maestro FastAPI app. It provides remote repo hosting for CLI clients using `muse push` and `muse pull`.

### DB Tables

| Table | Purpose |
|-------|---------|
| `musehub_repos` | Remote repos (name, visibility, owner) |
| `musehub_branches` | Branch pointers inside a repo |
| `musehub_commits` | Commits pushed from CLI clients |
| `musehub_objects` | Binary artifact metadata (MIDI, MP3, WebP piano rolls) |
| `musehub_issues` | Issue tracker entries per repo |
| `musehub_pull_requests` | Pull requests proposing branch merges |

### Module Map

```
maestro/
├── db/musehub_models.py                  — SQLAlchemy ORM models
├── models/musehub.py                     — Pydantic v2 request/response models
├── services/musehub_repository.py        — Async DB queries for repos/branches/commits
├── services/musehub_issues.py            — Async DB queries for issues (single point of DB access)
├── services/musehub_pull_requests.py     — Async DB queries for PRs (single point of DB access)
├── services/musehub_sync.py              — Push/pull sync protocol (ingest_push, compute_pull_delta)
└── api/routes/musehub/
    ├── __init__.py                       — Composes sub-routers under /musehub prefix
    ├── repos.py                          — Repo/branch/commit route handlers
    ├── issues.py                         — Issue tracking route handlers
    ├── pull_requests.py                  — Pull request route handlers
    └── sync.py                           — Push/pull sync route handlers
```

### Endpoints

#### Repos, Branches, Commits

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/musehub/repos` | Create remote repo |
| GET | `/api/v1/musehub/repos/{id}` | Get repo metadata |
| GET | `/api/v1/musehub/repos/{id}/branches` | List branches |
| GET | `/api/v1/musehub/repos/{id}/commits` | List commits (newest first) |

#### Issues

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/musehub/repos/{id}/issues` | Open a new issue (`state: open`) |
| GET | `/api/v1/musehub/repos/{id}/issues` | List issues (`?state=open\|closed\|all`, `?label=<string>`) |
| GET | `/api/v1/musehub/repos/{id}/issues/{number}` | Get a single issue by per-repo number |
| POST | `/api/v1/musehub/repos/{id}/issues/{number}/close` | Close an issue |

#### Pull Requests

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/musehub/repos/{id}/pull-requests` | Open a PR proposing to merge `from_branch` into `to_branch` |
| GET | `/api/v1/musehub/repos/{id}/pull-requests` | List PRs (`?state=open\|merged\|closed\|all`) |
| GET | `/api/v1/musehub/repos/{id}/pull-requests/{pr_id}` | Get a single PR by ID |
| POST | `/api/v1/musehub/repos/{id}/pull-requests/{pr_id}/merge` | Merge an open PR |

#### Sync Protocol

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/musehub/repos/{id}/push` | Upload commits and objects (fast-forward enforced) |
| POST | `/api/v1/musehub/repos/{id}/pull` | Fetch missing commits and objects |

All endpoints require `Authorization: Bearer <token>`. See [api.md](../reference/api.md#muse-hub-api) for full field docs.

### Issue Workflow

Issues let musicians track production problems and creative tasks within a repo, keeping feedback close to the music data rather than in out-of-band chat.

- **Issue numbers** are sequential per repo (1, 2, 3…) and independent across repos.
- **Labels** are free-form strings — e.g. `bug`, `musical`, `timing`, `mix`. No validation at MVP.
- **States:** `open` (default on creation) → `closed` (via the close endpoint). No re-open at MVP.
- **Filtering:** `GET /issues?state=all` includes both open and closed; `?label=bug` narrows by label.

### Pull Request Workflow

Pull requests let musicians propose merging one branch variation into another, enabling async review before incorporating changes into the canonical arrangement.

- **States:** `open` (on creation) → `merged` (via merge endpoint) | `closed` (future: manual close).
- **Merge strategy:** Only `merge_commit` at MVP. Creates a real merge commit on `to_branch` with two parent IDs (`[to_branch head, from_branch head]`), then advances the `to_branch` head pointer.
- **Validation:** `from_branch == to_branch` → 422. Missing `from_branch` → 404. Already merged/closed → 409 on merge attempt.
- **Filtering:** `GET /pull-requests?state=open` returns only open PRs. Default (`state=all`) returns all states.

### Sync Protocol Design

The push/pull protocol is intentionally simple for MVP:

#### Push — fast-forward enforcement

A push is accepted when one of the following is true:
1. The branch has no head yet (first push).
2. `headCommitId` equals the current remote head (no-op).
3. The current remote head appears in the ancestry graph of the pushed commits — i.e. the client built on top of the remote head.

When none of these conditions hold the push is **rejected with HTTP 409** and body `{"error": "non_fast_forward"}`. Set `force: true` in the request to overwrite the remote head regardless (equivalent to `git push --force`).

Commits and objects are **upserted by ID** — re-pushing the same content is safe and idempotent.

#### Pull — exclusion-list delta

The client sends `haveCommits` and `haveObjects` as exclusion lists. The Hub returns all commits for the requested branch and all objects for the repo that are NOT in those lists. No ancestry traversal is performed — the client receives the full delta in one response.

**MVP limitation:** Large objects (> 1 MB) are base64-encoded inline. Pre-signed URL upload is planned as a follow-up.

#### Object storage

Binary artifact bytes are written to disk at:

```
<settings.musehub_objects_dir>/<repo_id>/<object_id_with_colon_replaced_by_dash>
```

Default: `/data/musehub/objects`. Mount this path on a persistent volume in production.

Only metadata (`object_id`, `path`, `size_bytes`, `disk_path`) is stored in Postgres; the bytes live on disk.

### Architecture Boundary

Service modules are the only place that touches `musehub_*` tables:
- `musehub_repository.py` → `musehub_repos`, `musehub_branches`, `musehub_commits`
- `musehub_issues.py` → `musehub_issues`
- `musehub_pull_requests.py` → `musehub_pull_requests`
- `musehub_sync.py` → `musehub_commits`, `musehub_objects`, `musehub_branches` (sync path only)

Route handlers delegate all persistence to the service layer. No business logic in route handlers.

---

## Maestro → Muse Integration: Generate → Commit Pipeline

The stress test (`scripts/e2e/stress_test.py`) produces music artifacts in a
deterministic `muse-work/` layout consumable directly by `muse commit`.

### Output Contract (`--output-dir ./muse-work`)

```
muse-work/
  tracks/<instrument_combo>/<genre>_<bars>b_<composition_id>.mid
  renders/<genre>_<bars>b_<composition_id>.mp3
  previews/<genre>_<bars>b_<composition_id>.webp
  meta/<genre>_<bars>b_<composition_id>.json
muse-batch.json   (written next to muse-work/, i.e. in the repo root)
```

### `muse-batch.json` Schema

```json
{
  "run_id": "stress-20260227_172919",
  "generated_at": "2026-02-27T17:29:19Z",
  "commit_message_suggestion": "feat: 2-genre stress test (jazz, house)",
  "files": [
    {
      "path": "muse-work/tracks/drums_bass/jazz_4b_stress-20260227_172919-0000.mid",
      "role": "midi",
      "genre": "jazz",
      "bars": 4,
      "cached": false
    }
  ],
  "provenance": {
    "prompt": "stress_test.py --quick --genre jazz,house",
    "model": "storpheus",
    "seed": "stress-20260227_172919",
    "storpheus_version": "1.0.0"
  }
}
```

**Field rules:**
- `files[].path` — relative to repo root, always starts with `muse-work/`
- `files[].role` — one of `"midi"`, `"mp3"`, `"webp"`, `"meta"`
- `files[].cached` — `true` when the result was served from the Storpheus cache
- Failed generations are **omitted** from `files[]`; only successful results appear
- Cache hits **are included** in `files[]` with `"cached": true`

### Fast-Path Commit: `muse commit --from-batch`

```bash
# Run stress test → write muse-work/ layout + muse-batch.json
docker compose exec storpheus python scripts/e2e/stress_test.py \
    --quick --genre jazz,house --flush --output-dir ./muse-work

# Commit only the files produced by this run, using the suggested message
muse commit --from-batch muse-batch.json
```

`muse commit --from-batch <path>`:
1. Reads `muse-batch.json` from `<path>`
2. Uses `commit_message_suggestion` as the commit message (overrides `-m`)
3. Builds the snapshot manifest **restricted to files listed in `files[]`** — the rest of `muse-work/` is excluded
4. Proceeds with the standard commit pipeline (snapshot → DB → HEAD pointer update)

The `-m` flag is optional when `--from-batch` is present.  If both are supplied,
`--from-batch`'s suggestion wins.

### Workflow Summary

```
stress_test.py --output-dir ./muse-work
       │
       ├── saves artifacts → muse-work/{tracks,renders,previews,meta}/
       └── emits muse-batch.json (manifest + commit_message_suggestion)
              │
              ▼
muse commit --from-batch muse-batch.json
       │
       ├── reads batch → restrict snapshot to listed files
       ├── uses commit_message_suggestion
       └── creates versioned commit in Postgres
```

---

## Muse CLI — Music Analysis Command Reference

These commands expose musical dimensions across the commit graph — the layer that
makes Muse fundamentally different from Git. Each command is consumed by AI agents
to make musically coherent generation decisions. Every flag is part of a stable
CLI contract; stub implementations are clearly marked.

**Agent pattern:** Run with `--json` to get machine-readable output. Pipe into
`muse context` for a unified musical state document.

---

### `muse dynamics`

**Purpose:** Analyze the velocity (loudness) profile of a commit across all instrument
tracks. The primary tool for understanding the dynamic arc of an arrangement and
detecting flat, robotic, or over-compressed MIDI.

**Usage:**
```bash
muse dynamics [<commit>] [OPTIONS]
```

**Flags:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `COMMIT` | positional | HEAD | Commit ref to analyze |
| `--track TEXT` | string | all tracks | Case-insensitive prefix filter (e.g. `--track bass`) |
| `--section TEXT` | string | — | Restrict to a named section/region (planned) |
| `--compare COMMIT` | string | — | Side-by-side comparison with another commit (planned) |
| `--history` | flag | off | Show dynamics for every commit in branch history (planned) |
| `--peak` | flag | off | Show only tracks whose peak velocity exceeds the branch average |
| `--range` | flag | off | Sort output by velocity range descending |
| `--arc` | flag | off | When combined with `--track`, treat its value as an arc label filter |
| `--json` | flag | off | Emit structured JSON for agent consumption |

**Arc labels:**

| Label | Meaning |
|-------|---------|
| `flat` | Velocity variance < 10; steady throughout |
| `crescendo` | Monotonically rising from start to end |
| `decrescendo` | Monotonically falling from start to end |
| `terraced` | Step-wise plateaus; sudden jumps between stable levels |
| `swell` | Rises then falls (arch shape) |

**Output example (text):**
```
Dynamic profile — commit a1b2c3d4  (HEAD -> main)

Track      Avg Vel  Peak  Range  Arc
---------  -------  ----  -----  -----------
drums           88   110     42  terraced
bass            72    85     28  flat
keys            64    95     56  crescendo
lead            79   105     38  swell
```

**Output example (`--json`):**
```json
{
  "commit": "a1b2c3d4",
  "branch": "main",
  "tracks": [
    {"track": "drums", "avg_velocity": 88, "peak_velocity": 110, "velocity_range": 42, "arc": "terraced"}
  ]
}
```

**Result type:** `TrackDynamics` — fields: `name`, `avg_velocity`, `peak_velocity`, `velocity_range`, `arc`

**Agent use case:** Before generating a new layer, an agent calls `muse dynamics --json` to understand the current velocity landscape. If the arrangement is `flat` across all tracks, the agent adds velocity variation to the new part. If the arc is `crescendo`, the agent ensures the new layer contributes to rather than fights the build.

**Implementation:** `maestro/muse_cli/commands/dynamics.py` — `_dynamics_async` (injectable async core), `TrackDynamics` (result entity), `_render_table` / `_render_json` (renderers). Exit codes: 0 success, 2 outside repo, 3 internal.

> **Stub note:** Arc classification and velocity statistics are placeholder values. Full implementation requires MIDI note velocity extraction from committed snapshot objects (future: Storpheus MIDI parse route).

---

### `muse swing`

**Purpose:** Measure or annotate the swing factor of a commit — the ratio that
distinguishes a straight 8th-note grid from a shuffled jazz feel. Swing is one
of the most musically critical dimensions and is completely invisible to Git.

**Usage:**
```bash
muse swing [<commit>] [OPTIONS]
```

**Flags:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `COMMIT` | positional | working tree | Commit SHA to analyze |
| `--set FLOAT` | float | — | Annotate with an explicit swing factor (0.5–0.67) |
| `--detect` | flag | on | Detect and display the swing factor (default) |
| `--track TEXT` | string | all | Restrict to a named MIDI track (e.g. `--track bass`) |
| `--compare COMMIT` | string | — | Compare HEAD swing against another commit |
| `--history` | flag | off | Show swing history for the current branch |
| `--json` | flag | off | Emit machine-readable JSON |

**Swing factor scale:**

| Range | Label | Feel |
|-------|-------|------|
| < 0.53 | Straight | Equal 8th notes — pop, EDM, quantized |
| 0.53–0.58 | Light | Subtle shuffle — R&B, Neo-soul |
| 0.58–0.63 | Medium | Noticeable swing — jazz, hip-hop |
| ≥ 0.63 | Hard | Triplet feel — bebop, heavy jazz |

**Output example (text):**
```
Swing factor: 0.55 (Light)
Commit: a1b2c3d4  Branch: main
Track: all
```

**Output example (`--json`):**
```json
{"factor": 0.55, "label": "Light", "commit": "a1b2c3d4", "branch": "main", "track": "all"}
```

**Result type:** `dict` with keys `factor` (float), `label` (str), `commit` (str), `branch` (str), `track` (str). Future: typed `SwingResult` dataclass.

**Agent use case:** An AI generating a bass line runs `muse swing --json` to know whether to quantize straight or add shuffle. A Medium swing result means the bass should land slightly behind the grid to stay in pocket with the existing drum performance.

**Implementation:** `maestro/muse_cli/commands/swing.py` — `swing_label()`, `_swing_detect_async()`, `_swing_history_async()`, `_swing_compare_async()`, formatters. Exit codes: 0 success, 1 invalid `--set` value, 2 outside repo.

> **Stub note:** Returns a placeholder factor of 0.55. Full implementation requires onset-to-onset ratio measurement from committed MIDI note events (future: Storpheus MIDI parse route).

---

### `muse recall`

**Purpose:** Search the full commit history using natural language. Returns ranked
commits whose messages best match the query. The musical memory retrieval command —
"find me that arrangement I made three months ago."

**Usage:**
```bash
muse recall "<description>" [OPTIONS]
```

**Flags:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `QUERY` | positional | required | Natural-language description of what to find |
| `--limit N` | int | 5 | Maximum results to return |
| `--threshold FLOAT` | float | 0.6 | Minimum similarity score (0.0–1.0) |
| `--branch TEXT` | string | all branches | Restrict search to a specific branch |
| `--since DATE` | `YYYY-MM-DD` | — | Only search commits after this date |
| `--until DATE` | `YYYY-MM-DD` | — | Only search commits before this date |
| `--json` | flag | off | Emit structured JSON array |

**Scoring (current stub):** Normalized keyword overlap coefficient — `|Q ∩ M| / |Q|` — where Q is the set of query tokens and M is the set of message tokens. Score 1.0 means every query word appeared in the commit message.

**Output example (text):**
```
Recall: "dark jazz bassline"
keyword match · threshold 0.60 · limit 5

  1. [a1b2c3d4]  2026-02-15 22:00  boom bap demo take 3      score 0.67
  2. [f9e8d7c6]  2026-02-10 18:30  jazz bass overdub session  score 0.50
```

**Result type:** `RecallResult` (TypedDict) — fields: `rank` (int), `score` (float), `commit_id` (str), `date` (str), `branch` (str), `message` (str)

**Agent use case:** An agent asked to "generate something like that funky bass riff from last month" calls `muse recall "funky bass" --json --limit 3` to retrieve the closest historical commits, then uses those as style references for generation.

**Implementation:** `maestro/muse_cli/commands/recall.py` — `RecallResult` (TypedDict), `_tokenize()`, `_score()`, `_recall_async()`. Exit codes: 0 success, 1 bad date format, 2 outside repo.

> **Stub note:** Uses keyword overlap. Full implementation: vector embeddings stored in Qdrant, cosine similarity retrieval. The CLI interface will not change when vector search is added.

---

### `muse grep`

**Purpose:** Search all commits for a musical pattern — a note sequence, interval
pattern, or chord symbol. Currently searches commit messages; full MIDI content
search is the planned implementation.

**Usage:**
```bash
muse grep <pattern> [OPTIONS]
```

**Flags:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `PATTERN` | positional | required | Pattern to find (note seq, interval, chord, or text) |
| `--track TEXT` | string | — | Restrict to a named track (MIDI content search — planned) |
| `--section TEXT` | string | — | Restrict to a named section (planned) |
| `--transposition-invariant` | flag | on | Match in any key (planned for MIDI search) |
| `--rhythm-invariant` | flag | off | Match regardless of rhythm (planned) |
| `--commits` | flag | off | Output one commit ID per line instead of full table |
| `--json` | flag | off | Emit structured JSON array |

**Pattern formats (planned for MIDI content search):**

| Format | Example | Matches |
|--------|---------|---------|
| Note sequence | `"C4 E4 G4"` | Those exact pitches in sequence |
| Interval run | `"+4 +3"` | Major 3rd + minor 3rd (Cm arpeggio) |
| Chord symbol | `"Cm7"` | That chord anywhere in the arrangement |
| Text | `"verse piano"` | Commit message substring (current implementation) |

**Output example (text):**
```
Pattern: "dark jazz" (2 matches)

Commit       Branch   Committed            Message              Source
-----------  -------  -------------------  -------------------  -------
a1b2c3d4     main     2026-02-15 22:00     boom bap dark jazz   message
f9e8d7c6     main     2026-02-10 18:30     dark jazz bass       message
```

**Result type:** `GrepMatch` (dataclass) — fields: `commit_id` (str), `branch` (str), `message` (str), `committed_at` (str ISO-8601), `match_source` (str: `"message"` | `"branch"` | `"midi_content"`)

**Agent use case:** An agent searching for prior uses of a Cm7 chord calls `muse grep "Cm7" --commits --json` to get a list of commits containing that chord. It can then pull those commits as harmonic reference material.

**Implementation:** `maestro/muse_cli/commands/grep_cmd.py` — registered as `muse grep`. `GrepMatch` (dataclass), `_load_all_commits()`, `_grep_async()`, `_render_matches()`. Exit codes: 0 success, 2 outside repo.

> **Stub note:** Pattern matched against commit messages only. MIDI content scanning (parsing note events from snapshot objects) is tracked as a follow-up issue.

---

### `muse ask`

**Purpose:** Natural language question answering over commit history. Ask questions
in plain English; Muse searches history and returns a grounded answer citing specific
commits. The conversational interface to musical memory.

**Usage:**
```bash
muse ask "<question>" [OPTIONS]
```

**Flags:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `QUESTION` | positional | required | Natural-language question about musical history |
| `--branch TEXT` | string | all | Restrict history to a branch |
| `--since DATE` | `YYYY-MM-DD` | — | Only consider commits after this date |
| `--until DATE` | `YYYY-MM-DD` | — | Only consider commits before this date |
| `--cite` | flag | off | Show full commit IDs in the answer (default: short IDs) |
| `--json` | flag | off | Emit structured JSON response |

**Output example (text):**
```
Based on Muse history (47 commits searched):
Commits matching your query: 3 found

  [a1b2c3d] 2026-02-15 22:00  boom bap dark jazz session
  [f9e8d7c] 2026-02-10 18:30  Add bass overdub — minor key
  [3b2a1f0] 2026-01-28 14:00  Initial tempo work at 118 BPM

Note: Full LLM-powered answer generation is a planned enhancement.
```

**Output example (`--json`):**
```json
{
  "question": "when did we work on jazz?",
  "total_searched": 47,
  "matches_found": 3,
  "commits": [{"id": "a1b2c3d4...", "short_id": "a1b2c3d", "date": "2026-02-15", "message": "..."}],
  "stub_note": "Full LLM answer generation is a planned enhancement."
}
```

**Result type:** `AnswerResult` (class) — fields: `question` (str), `total_searched` (int), `matches` (list[MuseCliCommit]), `cite` (bool). Methods: `.to_plain()`, `.to_json_dict()`.

**Agent use case:** An AI agent composing a bridge asks `muse ask "what was the emotional arc of the chorus?" --json`. The answer grounds the agent in the actual commit history of the project before it generates, preventing stylistic drift.

**Implementation:** `maestro/muse_cli/commands/ask.py` — `AnswerResult`, `_keywords()`, `_ask_async()`. Exit codes: 0 success, 1 bad date, 2 outside repo.

> **Stub note:** Keyword matching over commit messages. Full implementation: RAG over Qdrant musical context embeddings + LLM answer synthesis via OpenRouter (Claude Sonnet/Opus). CLI interface is stable and will not change when LLM is wired in.

---

### `muse session`

**Purpose:** Record and query recording session metadata — who played, when, where,
and what they intended to create. Sessions are stored as local JSON files (not in
Postgres), mirroring how Git stores config as plain files.

**Usage:**
```bash
muse session <subcommand> [OPTIONS]
```

**Subcommands:**

| Subcommand | Description |
|------------|-------------|
| `muse session start` | Open a new recording session |
| `muse session end` | Finalize the active session |
| `muse session log` | List all completed sessions, newest first |
| `muse session show <id>` | Print a specific session by ID (prefix match) |
| `muse session credits` | Aggregate participants across all sessions |

**`muse session start` flags:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--participants TEXT` | string | — | Comma-separated participant names |
| `--location TEXT` | string | — | Studio or location name |
| `--intent TEXT` | string | — | Creative intent for this session |

**`muse session end` flags:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--notes TEXT` | string | — | Session notes / retrospective |

**Session JSON schema** (stored in `.muse/sessions/<uuid>.json`):
```json
{
  "session_id": "<uuid4>",
  "started_at": "2026-02-27T22:00:00+00:00",
  "ended_at": "2026-02-27T02:30:00+00:00",
  "participants": ["Gabriel (producer)", "Sarah (keys)"],
  "location": "Studio A",
  "intent": "Piano overdubs for verse sections",
  "commits": [],
  "notes": "Great tone on the Steinway today."
}
```

**`muse session log` output:**
```
SESSION  2026-02-27T22:00  → 2026-02-27T02:30  2h30m
  Participants: Gabriel (producer), Sarah (keys)
  Location:     Studio A
  Intent:       Piano overdubs for verse sections
```

**`muse session credits` output:**
```
Gabriel (producer)  7 sessions
Sarah (keys)        3 sessions
Marcus (bass)       2 sessions
```

**Agent use case:** An AI agent summarizing a project's creative history calls `muse session credits --json` to attribute musical contributions. An AI generating liner notes reads `muse session log --json` to reconstruct the session timeline.

**Implementation:** `maestro/muse_cli/commands/session.py` — all synchronous (no DB, no async). Storage: `.muse/sessions/current.json` (active) → `.muse/sessions/<uuid>.json` (completed). Exit codes: 0 success, 1 user error (duplicate session, no active session, ambiguous ID), 2 outside repo, 3 internal.


---

## `muse tempo` — Read or Set the Tempo of a Commit

`muse tempo [<commit>] [--set <bpm>] [--history] [--json]` reads or annotates
the BPM of a specific commit.  Tempo (BPM) is the most fundamental rhythmic property
of a Muse project — this command makes it a first-class commit attribute.

### Flags

| Flag | Description |
|------|-------------|
| `[<commit>]` | Target commit SHA (full or abbreviated) or `HEAD` (default) |
| `--set <bpm>` | Annotate the commit with an explicit BPM (20–400 range) |
| `--history` | Show BPM timeline across all commits in the parent chain |
| `--json` | Emit machine-readable JSON instead of human-readable text |

### Tempo Resolution Order (read path)

1. **Annotated BPM** — explicitly set via `muse tempo --set` and stored in `commit_metadata.tempo_bpm`.
2. **Detected BPM** — auto-extracted from MIDI Set Tempo meta-events (FF 51 03) in the commit's snapshot files.
3. **None** — displayed as `--` when neither source is available.

### Tempo Storage (write path)

`--set` writes `{tempo_bpm: <float>}` into the `metadata` JSON column of the
`muse_cli_commits` table.  Other metadata keys in that column are preserved
(merge-patch semantics).  No new rows are created — only the existing commit row
is annotated.

### Schema

The `muse_cli_commits` table has a nullable `metadata` JSON column (added in
migration `0002_muse_cli_commit_metadata`).  Current keys:

| Key | Type | Set by |
|-----|------|--------|
| `tempo_bpm` | `float` | `muse tempo --set` |

### History Traversal

`--history` walks the full parent chain from the target commit (or HEAD),
collecting annotated BPM values and computing signed deltas between consecutive
commits:



Auto-detected BPM is shown on the single-commit read path but is not persisted,
so it does not appear in history (history only reflects explicitly set annotations).

### MIDI Tempo Parsing

`maestro/services/muse_tempo.extract_bpm_from_midi(data: bytes)` is a pure
function that scans a raw MIDI byte string for the Set Tempo meta-event:



The three `tt` bytes encode microseconds-per-beat as a 24-bit big-endian integer.
BPM = 60_000_000 / microseconds_per_beat.  Only the first event is returned;
`detect_all_tempos_from_midi` returns all events (used for rubato detection).

### Result Types

| Type | Module | Purpose |
|------|--------|---------|
| `MuseTempoResult` | `maestro.services.muse_tempo` | Single-commit tempo query result |
| `MuseTempoHistoryEntry` | `maestro.services.muse_tempo` | One row in a `--history` traversal |

### DB Helpers

| Helper | Module | Purpose |
|--------|--------|---------|
| `resolve_commit_ref` | `maestro.muse_cli.db` | Resolve HEAD / full SHA / abbreviated SHA to a `MuseCliCommit` |
| `set_commit_tempo_bpm` | `maestro.muse_cli.db` | Write `tempo_bpm` into `commit_metadata` (merge-patch) |

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | User error: unknown ref, BPM out of range |
| 2 | Outside a Muse repository |
| 3 | Internal error |

---

### Command Registration Summary

| Command | File | Status | Issue |
|---------|------|--------|-------|
| `muse dynamics` | `commands/dynamics.py` | ✅ stub (PR #130) | #120 |
| `muse swing` | `commands/swing.py` | ✅ stub (PR #131) | #121 |
| `muse recall` | `commands/recall.py` | ✅ stub (PR #135) | #122 |
| `muse tag` | `commands/tag.py` | ✅ implemented (PR #133) | #123 |
| `muse grep` | `commands/grep_cmd.py` | ✅ stub (PR #128) | #124 |
| `muse describe` | `commands/describe.py` | ✅ stub (PR #134) | #125 |
| `muse ask` | `commands/ask.py` | ✅ stub (PR #132) | #126 |
| `muse session` | `commands/session.py` | ✅ implemented (PR #129) | #127 |
| `muse tempo` | `commands/tempo.py` | ✅ fully implemented (PR TBD) | #116 |

All stub commands have stable CLI contracts. Full musical analysis (MIDI content
parsing, vector embeddings, LLM synthesis) is tracked as follow-up issues.

---

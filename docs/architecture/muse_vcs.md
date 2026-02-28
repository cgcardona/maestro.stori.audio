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
    ├── models.py         — MuseCliCommit, MuseCliSnapshot, MuseCliObject, MuseCliTag (SQLAlchemy)
    ├── db.py             — open_session, upsert/get helpers, get_head_snapshot_manifest, find_commits_by_prefix
    ├── tag.py            — muse tag ✅ add/remove/list/search (issue #123)
    ├── merge_engine.py   — find_merge_base(), diff_snapshots(), detect_conflicts(),
    │                        apply_merge(), read/write_merge_state(), MergeState dataclass
    ├── checkout.py       — muse checkout (stub — issue #34)
    ├── merge.py          — muse merge   ✅ fast-forward + 3-way merge (issue #35)
    ├── remote.py         — muse remote  (stub — issue #38)
    ├── push.py           — muse push    (stub — issue #38)
    ├── pull.py           — muse pull    (stub — issue #38)
    ├── open_cmd.py       — muse open    ✅ macOS artifact preview (issue #45)
    ├── play.py           — muse play    ✅ macOS audio playback via afplay (issue #45)
    ├── export.py         — muse export  ✅ snapshot export to MIDI/JSON/MusicXML/ABC/WAV (issue #112)
    ├── find.py           — muse find   ✅ search commit history by musical properties (issue #114)
    └── ask.py            — muse ask     ✅ natural language query over commit history (issue #126)
```

`maestro/muse_cli/export_engine.py` — `ExportFormat`, `MuseExportOptions`, `MuseExportResult`,
`StorpheusUnavailableError`, `filter_manifest`, `export_snapshot`, and per-format handlers
(`export_midi`, `export_json`, `export_musicxml`, `export_abc`, `export_wav`). See
`## muse export` section below.

`maestro/muse_cli/artifact_resolver.py` — `resolve_artifact_async()` / `resolve_artifact()`:
resolves a user-supplied path-or-commit-ID to a concrete `pathlib.Path` (see below).

The CLI delegates to existing `maestro/services/muse_*.py` service modules. Stub subcommands print "not yet implemented" and exit 0.

---

## `muse tag` — Music-Semantic Tagging

`muse tag` attaches free-form music-semantic labels to commits, enabling expressive search across
the composition history.

### Subcommands

| Command | Description |
|---------|-------------|
| `muse tag add <tag> [<commit>]` | Attach a tag (defaults to HEAD) |
| `muse tag remove <tag> [<commit>]` | Remove a tag (defaults to HEAD) |
| `muse tag list [<commit>]` | List all tags on a commit (defaults to HEAD) |
| `muse tag search <tag>` | Find commits carrying the tag; use trailing `:` for namespace prefix search |

### Tag namespaces

Tags are free-form strings. Conventional namespace prefixes aid search:

| Namespace | Example | Meaning |
|-----------|---------|---------|
| `emotion:` | `emotion:melancholic` | Emotional character |
| `stage:` | `stage:rough-mix` | Production stage |
| `ref:` | `ref:beatles` | Reference track or source |
| `key:` | `key:Am` | Musical key |
| `tempo:` | `tempo:120bpm` | Tempo annotation |
| *(free-form)* | `lo-fi` | Any other label |

### Storage

Tags are stored in the `muse_cli_tags` table (PostgreSQL):

```
muse_cli_tags
  tag_id     UUID PK
  repo_id    String(36)   — scoped per local repo
  commit_id  String(64)   — FK → muse_cli_commits.commit_id (CASCADE DELETE)
  tag        Text
  created_at DateTime
```

Tags are scoped to a `repo_id` so independent local repositories use separate tag spaces.
A commit can carry multiple tags. Adding the same tag twice is a no-op (idempotent).

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

---

## `muse arrange [<commit>]` — Arrangement Map (issue #115)

`muse arrange` displays the **arrangement matrix**: which instruments are active in which musical sections for a given commit.  This is the single most useful command for an AI orchestration agent — before generating a new string part, the agent can run `muse arrange --format json HEAD` to see exactly which sections already have strings, preventing doubling mistakes and enabling coherent orchestration decisions.

### Path Convention

Files committed to Muse must follow the three-level path convention to participate in the arrangement map:

```
muse-work/<section>/<instrument>/<filename>
```

| Level | Example | Description |
|-------|---------|-------------|
| `<section>` | `intro`, `verse`, `chorus`, `bridge`, `outro` | Musical section name (normalised to lowercase) |
| `<instrument>` | `drums`, `bass`, `strings`, `piano`, `vocals` | Instrument / track name |
| `<filename>` | `beat.mid`, `pad.mid` | The actual file |

Files with fewer than three path components are excluded from the arrangement map (they carry no section metadata).

Section aliases are normalised: `pre-chorus`, `pre_chorus`, and `prechoruse` all map to `prechorus`.

### Output Formats

**Text (default)**:

```
Arrangement Map — commit abc1234

            Intro  Verse  Chorus  Bridge  Outro
drums       ████   ████   ████    ████    ████
bass        ░░░░   ████   ████    ████    ████
piano       ████   ░░░░   ████    ░░░░    ████
strings     ░░░░   ░░░░   ████    ████    ░░░░
```

`████` = active (at least one file for that section/instrument pair).
`░░░░` = inactive (no files).

**JSON (`--format json`)** — structured, AI-agent-consumable:

```json
{
  "commit_id": "abc1234...",
  "sections": ["intro", "verse", "chorus", "bridge", "outro"],
  "instruments": ["bass", "drums", "piano", "strings"],
  "arrangement": {
    "drums": { "intro": true, "verse": true, "chorus": true },
    "strings": { "intro": false, "verse": false, "chorus": true }
  }
}
```

**CSV (`--format csv`)** — spreadsheet-ready rows with `0`/`1` cells.

## `muse describe` — Structured Musical Change Description

`muse describe [<commit>] [OPTIONS]` compares a commit against its parent (or two commits via `--compare`) and outputs a structured description of what changed at the snapshot level.

### Output example (standard depth)

```
Commit abc1234: "Add piano melody to verse"
Changed files: 2 (beat.mid, keys.mid)
Dimensions analyzed: structural (2 files modified)
Note: Full harmonic/melodic analysis requires muse harmony and muse motif (planned)
```

### Flags

| Flag | Default | Description |
|------|---------|-------------|
| `[COMMIT]` | `HEAD` | Target commit: HEAD, branch name, or commit-ID prefix |
| `--section TEXT` | none | Show only a specific section's instrumentation |
| `--track TEXT` | none | Show only a specific instrument's section participation |
| `--compare A --compare B` | — | Diff two arrangements (show added/removed cells) |
| `--density` | off | Show byte-size total per cell instead of binary active/inactive |
| `--format text\|json\|csv` | `text` | Output format |

### Compare Mode (`--compare`)

```
Arrangement Diff — abc1234 → def5678

            Intro  Verse  Chorus
drums        ████   ████   ████
strings     ░░░░   ░░░░  +████
piano       ████   ░░░░  -████
```

`+████` = cell added in commit-b.
`-████` = cell removed in commit-b.

### Density Mode (`--density`)

Each cell shows the total byte size of all files for that (section, instrument) pair.  Byte size correlates with note density for MIDI files and serves as a useful heuristic for AI orchestration agents:

```
            Intro   Verse   Chorus
drums       4,096   3,200   5,120
bass            -   1,024   2,048
```
| `<commit>` (positional) | HEAD | Commit to describe |
| `--compare A B` | — | Compare commit A against commit B explicitly |
| `--depth brief\|standard\|verbose` | `standard` | Output verbosity |
| `--dimensions TEXT` | — | Comma-separated dimension labels (informational, passed through to output) |
| `--json` | off | Output as JSON |
| `--auto-tag` | off | Add a heuristic tag based on change scope |

### Depth modes

| Depth | Output |
|-------|--------|
| `brief` | One-line: `Commit <id>: N file changes` |
| `standard` | Message, changed files list, inferred dimensions, LLM note |
| `verbose` | Full commit ID, parent ID, per-file M/A/D markers, dimensions |

### Implementation

| Layer | File | Responsibility |
|-------|------|----------------|
| Service | `maestro/services/muse_arrange.py` | `build_arrangement_matrix()`, diff, renderers |
| Command | `maestro/muse_cli/commands/arrange.py` | Typer callback + `_arrange_async` |
| App | `maestro/muse_cli/app.py` | Registration under `arrange` subcommand |

`_arrange_async` is fully injectable for unit tests (accepts a `root: pathlib.Path` and `session: AsyncSession`).

Exit codes: `0` success, `1` user error (unknown format, missing reference, ambiguous prefix), `2` outside a Muse repo, `3` internal error.

### Named Result Types

See `docs/reference/type_contracts.md`:
- `ArrangementCell` — per (section, instrument) data
- `ArrangementMatrix` — full matrix for one commit
- `ArrangementDiffCell` — change status for one cell
- `ArrangementDiff` — full diff between two matrices
| Command | `maestro/muse_cli/commands/describe.py` | Typer callback + `_describe_async` |
| Diff engine | `maestro/muse_cli/commands/describe.py` | `_diff_manifests()` |
| Renderers | `maestro/muse_cli/commands/describe.py` | `_render_brief/standard/verbose/result` |
| DB helpers | `maestro/muse_cli/db.py` | `get_commit_snapshot_manifest()` |

`_describe_async` is the injectable async core (tested directly without a running server).  Exit codes: 0 success, 1 user error (bad commit ID or wrong `--compare` count), 2 outside a Muse repo, 3 internal error.

**Result type:** `DescribeResult` (class) — fields: `commit_id` (str), `message` (str), `depth` (DescribeDepth), `parent_id` (str | None), `compare_commit_id` (str | None), `changed_files` (list[str]), `added_files` (list[str]), `removed_files` (list[str]), `dimensions` (list[str]), `auto_tag` (str | None). Methods: `.file_count()` → int, `.to_dict()` → dict[str, object]. See `docs/reference/type_contracts.md § DescribeResult`.

**Agent use case:** Before generating new material, an agent calls `muse describe --json` to understand what changed in the most recent commit. If a bass and melody file were both modified, the agent knows a harmonic rewrite occurred and adjusts generation accordingly. `--auto-tag` provides a quick `minor-revision` / `major-revision` signal without full MIDI analysis.

> **Planned enhancement:** Full harmonic, melodic, and rhythmic analysis (chord progression diffs, motif tracking, groove scoring) is tracked as a follow-up. Current output is purely structural — file-level snapshot diffs with no MIDI parsing.

---

## `muse export` — Export a Snapshot to External Formats

`muse export [<commit>] --format <format>` exports a Muse snapshot to a
file format usable outside the DAW.  This is a **read-only** operation —
no commit is created and no DB writes occur.  Given the same commit ID and
format, the output is always identical (deterministic).

### Usage

```
muse export [<commit>] --format <format> [OPTIONS]

Arguments:
  <commit>          Short commit ID prefix (default: HEAD).

Options:
  --format, -f      Target format (required): midi | json | musicxml | abc | wav
  --output, -o      Destination path (default: ./exports/<commit8>.<format>)
  --track TEXT      Export only files whose path contains TEXT (substring match).
  --section TEXT    Export only files whose path contains TEXT (substring match).
  --split-tracks    Write one file per MIDI track (MIDI only).
```

### Supported Formats

| Format     | Extension | Description |
|------------|-----------|-------------|
| `midi`     | `.mid`    | Copy raw MIDI files from the snapshot (lossless, native). |
| `json`     | `.json`   | Structured JSON index of snapshot files (AI/tooling consumption). |
| `musicxml` | `.xml`    | MusicXML for notation software (MuseScore, Sibelius, etc.). |
| `abc`      | `.abc`    | ABC notation for folk/traditional music tools. |
| `wav`      | `.wav`    | Audio render via Storpheus (requires Storpheus running). |

### Examples

```bash
# Export HEAD snapshot as MIDI
muse export --format midi --output /tmp/my-song.mid

# Export only the piano track from a specific commit
muse export a1b2c3d4 --format midi --track piano

# Export the chorus section as MusicXML
muse export --format musicxml --section chorus

# Export all tracks as separate MIDI files
muse export --format midi --split-tracks

# Export JSON note structure
muse export --format json --output /tmp/snapshot.json

# WAV render (Storpheus must be running)
muse export --format wav
```

### Implementation

| Component | Location |
|-----------|----------|
| CLI command | `maestro/muse_cli/commands/export.py` |
| Format engine | `maestro/muse_cli/export_engine.py` |
| Tests | `tests/muse_cli/test_export.py` |

`export_engine.py` provides:

- `ExportFormat` — enum of supported formats.
- `MuseExportOptions` — frozen dataclass with export settings.
- `MuseExportResult` — result dataclass listing written paths.
- `StorpheusUnavailableError` — raised when WAV export is attempted
  but Storpheus is unreachable (callers surface a clean error message).
- `filter_manifest()` — applies `--track` / `--section` filters.
- `export_snapshot()` — top-level dispatcher.
- Format handlers: `export_midi`, `export_json`, `export_musicxml`, `export_abc`, `export_wav`.
- MIDI conversion helpers: `_midi_to_musicxml`, `_midi_to_abc` (minimal, best-effort).

### WAV Export and Storpheus Dependency

`--format wav` delegates audio rendering to the Storpheus service
(port 10002).  Before attempting any conversion, `export_wav` performs
a synchronous health check against `GET /health`.  If Storpheus is not
reachable or returns a non-200 response, `StorpheusUnavailableError` is
raised and the CLI exits with a clear human-readable error:

```
❌ WAV export requires Storpheus.
Storpheus is not reachable at http://localhost:10002: Connection refused
Start Storpheus (docker compose up storpheus) and retry.
```

### Filter Semantics

`--track` and `--section` are **case-insensitive substring matches** against
the full relative path of each file in the snapshot manifest.  Both filters
are applied with AND semantics: a file must match all provided filters to be
included.

```
manifest:
  chorus/piano/take1.mid
  verse/piano/take1.mid
  chorus/bass/take1.mid

--track piano → chorus/piano/take1.mid, verse/piano/take1.mid
--section chorus → chorus/piano/take1.mid, chorus/bass/take1.mid
--track piano --section chorus → chorus/piano/take1.mid
```

### Postgres State

Export is read-only.  It reads `muse_cli_commits` and `muse_cli_snapshots`
but writes nothing to the database.

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

## `muse find` — Search Commit History by Musical Properties

`muse find` is the musical grep: it queries the full commit history for the
current repository and returns commits whose messages match musical criteria.
All filter flags combine with **AND logic** — a commit must satisfy every
supplied criterion to appear in results.

### Command Flags

| Flag | Example | Description |
|------|---------|-------------|
| `--harmony <query>` | `"key=Eb"`, `"mode=minor"` | Harmonic filter |
| `--rhythm <query>` | `"tempo=120-130"`, `"meter=7/8"` | Rhythmic filter |
| `--melody <query>` | `"shape=arch"`, `"motif=main-theme"` | Melodic filter |
| `--structure <query>` | `"has=bridge"`, `"form=AABA"` | Structural filter |
| `--dynamic <query>` | `"avg_vel>80"`, `"arc=crescendo"` | Dynamic filter |
| `--emotion <tag>` | `melancholic` | Emotion tag |
| `--section <text>` | `"chorus"` | Named section filter |
| `--track <text>` | `"bass"` | Track presence filter |
| `--since <date>` | `"2026-01-01"` | Commits after this date (UTC) |
| `--until <date>` | `"2026-03-01"` | Commits before this date (UTC) |
| `--limit N` / `-n N` | `20` (default) | Cap results |
| `--json` | — | Machine-readable JSON output |

### Query DSL

#### Equality match (default)

All property filters do a **case-insensitive substring match** against the
commit message:

```
muse find --harmony "key=F minor"
```

Finds every commit whose message contains the string `key=F minor` (any case).

#### Numeric range match

When the value portion of a `key=value` expression contains two numbers
separated by a hyphen (`low-high`), the filter extracts the numeric value of
the key from the message and checks whether it falls within the range
(inclusive):

```
muse find --rhythm "tempo=120-130"
```

Matches commits whose message contains `tempo=<N>` where 120 ≤ N ≤ 130.

### Output Formats

#### Default (text)

One commit block per match, newest-first:

```
commit a1b2c3d4...
Branch: main
Parent: f9e8d7c6
Date:   2026-02-27 17:30:00

    ambient sketch, key=F minor, tempo=90 bpm

```

#### `--json` output

A JSON array of commit objects:

```json
[
  {
    "commit_id": "a1b2c3d4...",
    "branch": "main",
    "message": "ambient sketch, key=F minor, tempo=90 bpm",
    "author": "",
    "committed_at": "2026-02-27T17:30:00+00:00",
    "parent_commit_id": "f9e8d7c6...",
    "snapshot_id": "bac947cf..."
  }
]
```

### Examples

```bash
# All commits in F minor
muse find --harmony "key=F minor"

# Up-tempo commits in a date window
muse find --rhythm "tempo=120-130" --since "2026-01-01"

# Melancholic commits that include a bridge, as JSON
muse find --emotion melancholic --structure "has=bridge" --json

# Bass track presence, capped at 10 results
muse find --track bass --limit 10
```

### Architecture

- **Service:** `maestro/services/muse_find.py`
  - `MuseFindQuery` — frozen dataclass of all search criteria
  - `MuseFindCommitResult` — a single matching commit
  - `MuseFindResults` — container with matches, total scanned, and the query
  - `search_commits(session, repo_id, query)` — async search function
- **CLI command:** `maestro/muse_cli/commands/find.py`
  - `_find_async(root, session, query, output_json)` — injectable core (tested directly)
  - Registered in `maestro/muse_cli/app.py` as `find`

### Postgres Behaviour

Read-only operation — no writes.  Plain-text filters are pushed to SQL via
`ILIKE` for efficiency; numeric range filters are applied in Python after
the SQL result set is fetched.  `committed_at` date range filters use SQL
`>=` / `<=` comparisons.

---

## `muse session` — Recording Session Metadata

**Purpose:** Track who was in the room, where you recorded, and why — purely as local JSON files. Sessions are decoupled from VCS commits: they capture the human context around a recording block and can later reference commit IDs that were created during that time.

Sessions live in `.muse/sessions/` as plain JSON files — no database tables, no Alembic migrations. This mirrors git's philosophy of storing metadata as plain files rather than in a relational store.

### Subcommands

| Subcommand | Flags | Purpose |
|------------|-------|---------|
| `muse session start` | `--participants`, `--location`, `--intent` | Open a new session; writes `current.json`. Only one active session at a time. |
| `muse session end` | `--notes` | Finalise active session; moves `current.json` → `<uuid>.json`. |
| `muse session log` | _(none)_ | List all completed sessions, newest first. |
| `muse session show <id>` | _(prefix match supported)_ | Print full JSON for a specific completed session. |
| `muse session credits` | _(none)_ | Aggregate participants across all completed sessions, sorted by count descending. |

### Storage Layout

```
.muse/
    sessions/
        current.json           ← active session (exists only while recording)
        <session-uuid>.json    ← one file per completed session
```

### Session JSON Schema (`MuseSessionRecord`)

```json
{
    "session_id":      "<uuid4>",
    "schema_version":  "1",
    "started_at":      "2026-02-27T15:49:19+00:00",
    "ended_at":        "2026-02-27T17:30:00+00:00",
    "participants":    ["Alice", "Bob"],
    "location":        "Studio A",
    "intent":          "Record the bridge",
    "commits":         ["abc123", "def456"],
    "notes":           "Nailed the third take."
}
```

The `commits` list is populated externally (e.g., by `muse commit` in a future integration); it starts empty.

### Output Examples

**`muse session log`**

```
3f2a1b0c  2026-02-27T15:49:19  →  2026-02-27T17:30:00  [Alice, Bob]
a1b2c3d4  2026-02-26T10:00:00  →  2026-02-26T12:00:00  []
```

**`muse session credits`**

```
Session credits:
  Alice                           2 sessions
  Bob                             1 session
  Carol                           1 session
```

### Result Type

`MuseSessionRecord` — TypedDict defined in `maestro/muse_cli/commands/session.py`. See `docs/reference/type_contracts.md` for the full field table.

### Atomicity

`muse session end` writes a temp file (`.tmp-<uuid>.json`) in the same directory, then renames it to `<uuid>.json` before unlinking `current.json`. This guarantees that a crash between write and cleanup never leaves both `current.json` and `<uuid>.json` present simultaneously, which would block future `muse session start` calls.

### Agent Use Case

An AI composition agent can:
- Call `muse session start --participants "Claude,Gabriel" --intent "Groove track"` before a generation run.
- Call `muse session end --notes "Generated 4 variations"` after the run completes.
- Query `muse session credits` to see which participants have contributed most across the project's history.

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

## Muse CLI — Plumbing Command Reference

Plumbing commands expose the raw object model and allow scripted or programmatic
construction of history without the side-effects of porcelain commands.  They
mirror the design of `git commit-tree`, `git update-ref`, and `git hash-object`.

AI agents use plumbing commands when they need to build commit graphs
programmatically — for example when replaying a merge, synthesising history from
an external source, or constructing commits without changing the working branch.

---

### `muse commit-tree`

**Purpose:** Create a raw commit object directly from an existing `snapshot_id`
and explicit metadata.  Does not walk the filesystem, does not update any branch
ref, does not touch `.muse/HEAD`.  Use `muse update-ref` (planned) to associate
the resulting commit with a branch.

**Usage:**
```bash
muse commit-tree <snapshot_id> -m <message> [OPTIONS]
```

**Flags:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `snapshot_id` | positional | — | ID of an existing snapshot row in the database |
| `-m / --message TEXT` | string | required | Commit message |
| `-p / --parent TEXT` | string | — | Parent commit ID. Repeat for merge commits (max 2) |
| `--author TEXT` | string | `[user] name` from `.muse/config.toml` or `""` | Author name |

**Output example:**
```
a3f8c21d4e9b0712c5d6f7a8e3b2c1d0a4f5e6b7c8d9e0f1a2b3c4d5e6f7a8b9
```

The commit ID (64-char SHA-256 hex) is printed to stdout.  Pipe it to
`muse update-ref` to advance a branch ref.

**Result type:** `CommitTreeResult` — fields: `commit_id` (str, 64-char hex).

**Idempotency contract:** The commit ID is derived deterministically from
`(parent_ids, snapshot_id, message, author)` with **no timestamp** component.
Repeating the same call returns the same `commit_id` without inserting a
duplicate row.  This makes `muse commit-tree` safe to call in retry loops and
idempotent scripts.

**Agent use case:** An AI music generation agent that needs to construct a merge
commit (e.g. combining the groove from branch A with the lead from branch B)
without moving either branch pointer:

```bash
SNAP=$(muse write-tree)                            # planned plumbing command
COMMIT=$(muse commit-tree "$SNAP" -m "Merge groove+lead" -p "$A_HEAD" -p "$B_HEAD")
muse update-ref refs/heads/merge-candidate "$COMMIT"  # planned
```

**Error cases:**
- `snapshot_id` not found → exits 1 with a clear message
- More than 2 `-p` parents → exits 1 (DB model stores at most 2)
- Not inside a Muse repo → exits 2

**Implementation:** `maestro/muse_cli/commands/commit_tree.py`

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
## `muse swing` — Swing Factor Analysis and Annotation

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
(stub — full MIDI analysis pending)
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

### `muse revert`

**Purpose:** Create a new commit that undoes a prior commit without rewriting history. The safe undo: given commit C with parent P, `muse revert <commit>` creates a forward commit whose snapshot is P's state (the world before C was applied). An AI agent uses this after discovering a committed arrangement degraded the score — rather than resetting (which loses history), the revert preserves the full audit trail.

**Usage:**
```bash
muse revert <commit> [OPTIONS]
```

**Flags:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `COMMIT` | positional | required | Commit ID to revert (full or abbreviated SHA) |
| `--no-commit` | flag | off | Apply the inverse changes to muse-work/ without creating a new commit |
| `--track TEXT` | string | — | Scope the revert to paths under `tracks/<track>/` only |
| `--section TEXT` | string | — | Scope the revert to paths under `sections/<section>/` only |

**Output example (full revert):**
```
✅ [main a1b2c3d4] Revert 'bad drum arrangement'
```

**Output example (scoped revert):**
```
✅ [main b2c3d4e5] Revert 'bad drum arrangement' (scoped to 2 path(s))
```

**Output example (--no-commit):**
```
✅ Staged revert (--no-commit). Files removed:
   deleted: tracks/drums/fill.mid
```

**Result type:** `RevertResult` (dataclass, frozen) — fields:
- `commit_id` (str): New commit ID (empty string when `--no-commit` or noop).
- `target_commit_id` (str): Commit that was reverted.
- `parent_commit_id` (str): Parent of the reverted commit (whose snapshot was restored).
- `revert_snapshot_id` (str): Snapshot ID of the reverted state.
- `message` (str): Auto-generated commit message (`"Revert '<original message>'"`)
- `no_commit` (bool): Whether the revert was staged only.
- `noop` (bool): True when reverting would produce no change.
- `scoped_paths` (tuple[str, ...]): Paths selectively reverted (empty = full revert).
- `paths_deleted` (tuple[str, ...]): Files removed from muse-work/ during `--no-commit`.
- `paths_missing` (tuple[str, ...]): Files that could not be auto-restored (no object bytes).
- `branch` (str): Branch on which the revert commit was created.

**Agent use case:** An agent that evaluates generated arrangements after each commit can run `muse log --json` to detect quality regressions, then call `muse revert <bad_commit>` to undo the offending commit and resume generation from the prior good state. For instrument-specific corrections, `--track drums` limits the revert to drum tracks only, preserving bass and melodic changes.

**Blocking behaviour:** Blocked during an in-progress merge with unresolved conflicts — exits 1 with a clear message directing the user to resolve conflicts first.

**Object store limitation:** The Muse CLI stores file manifests (path→sha256) in Postgres but does not retain raw file bytes. For `--no-commit`, files that should be restored but whose bytes are no longer in `muse-work/` are listed as warnings in `paths_missing`. The commit-only path (default) is unaffected — it references an existing snapshot ID directly with no file restoration needed.

**Implementation:** `maestro/muse_cli/commands/revert.py` (Typer CLI), `maestro/services/muse_revert.py` (`_revert_async`, `compute_revert_manifest`, `apply_revert_to_workdir`, `RevertResult`).

---

### `muse grep`

**Purpose:** Search all commits for a musical pattern — a note sequence, interval
pattern, or chord symbol. Currently searches commit messages; full MIDI content
search is the planned implementation.
{"factor": 0.55, "label": "Light", "commit": "a1b2c3d4", "branch": "main", "track": "all", "source": "stub"}
```

**Result type:** `SwingDetectResult` (TypedDict) — fields: `factor` (float),
`label` (str), `commit` (str), `branch` (str), `track` (str), `source` (str).
`--compare` returns `SwingCompareResult` — fields: `head` (SwingDetectResult),
`compare` (SwingDetectResult), `delta` (float). See
`docs/reference/type_contracts.md § Muse CLI Types`.

**Agent use case:** An AI generating a bass line runs `muse swing --json` to
know whether to quantize straight or add shuffle. A Medium swing result means
the bass should land slightly behind the grid to stay in pocket with the
existing drum performance.

**Implementation:** `maestro/muse_cli/commands/swing.py` — `swing_label()`,
`_swing_detect_async()`, `_swing_history_async()`, `_swing_compare_async()`,
`_format_detect()`, `_format_history()`, `_format_compare()`. Exit codes:
0 success, 1 invalid `--set` value, 2 outside repo, 3 internal error.

> **Stub note:** Returns a placeholder factor of 0.55. Full implementation
> requires onset-to-onset ratio measurement from committed MIDI note events
> (future: Storpheus MIDI parse route).

---

## `muse chord-map` — Chord Progression Timeline

`muse chord-map [<commit>]` extracts and displays the chord timeline of a
specific commit — showing *when* each chord occurs in the arrangement, not
just which chords are present.  This is the foundation for AI-generated
harmonic analysis and chord-substitution suggestions.

**Purpose:** Give AI agents a precise picture of the harmonic structure at any
commit so they can reason about the progression in time, propose substitutions,
or detect tension/resolution cycles.

### Flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `COMMIT` | positional | HEAD | Commit ref to analyse. |
| `--section TEXT` | string | — | Scope to a named section/region. |
| `--track TEXT` | string | — | Scope to a specific track (e.g. piano for chord voicings). |
| `--bar-grid / --no-bar-grid` | flag | on | Align chord events to musical bar numbers. |
| `--format FORMAT` | string | `text` | Output format: `text`, `json`, or `mermaid`. |
| `--voice-leading` | flag | off | Show how individual notes move between consecutive chords. |

### Output example

**Text (default, `--bar-grid`):**

```
Chord map -- commit a1b2c3d4  (HEAD -> main)

Bar  1: Cmaj9       ########
Bar  2: Am11        ########
Bar  3: Dm7         ####  Gsus4       ####
Bar  4: G7          ########
Bar  5: Cmaj9       ########

(stub -- full MIDI chord detection pending)
```

**With `--voice-leading`:**

```
Chord map -- commit a1b2c3d4  (HEAD -> main)

Bar  2: Cmaj9    -> Am11  (E->E, G->G, B->A, D->C)
Bar  3: Am11     -> Dm7   (A->D, C->C, E->F, G->A)
...
```

**JSON (`--format json`):**

```json
{
  "commit": "a1b2c3d4",
  "branch": "main",
  "track": "all",
  "section": "",
  "chords": [
    { "bar": 1, "beat": 1, "chord": "Cmaj9", "duration": 1.0, "track": "keys" },
    { "bar": 2, "beat": 1, "chord": "Am11",  "duration": 1.0, "track": "keys" }
  ],
  "voice_leading": []
}
```

**Mermaid (`--format mermaid`):**

```
timeline
    title Chord map -- a1b2c3d4
    section Bar 1
        Cmaj9
    section Bar 2
        Am11
```

### Result type

`muse chord-map` returns a `ChordMapResult` TypedDict (see
`docs/reference/type_contracts.md § ChordMapResult`).  Each chord event is a
`ChordEvent`; each voice-leading step is a `VoiceLeadingStep`.

### Agent use case

An AI agent writing a counter-melody calls `muse chord-map HEAD --format json`
to retrieve the exact bar-by-bar harmonic grid.  It then selects chord tones
that land on the strong beats.  With `--voice-leading`, the agent can also
detect smooth inner-voice motion and mirror it in the new part.

**Implementation:** `maestro/muse_cli/commands/chord_map.py` —
`_chord_map_async()`, `_render_text()`, `_render_json()`, `_render_mermaid()`.
Exit codes: 0 success, 1 invalid `--format`, 2 outside repo, 3 internal error.

> **Stub note:** Returns a placeholder I-vi-ii-V-I progression. Full
> implementation requires chord-detection from committed MIDI note events
> (future: Storpheus MIDI parse route).

---

## `muse key` — Read or Annotate the Musical Key of a Commit

`muse key` reads or annotates the tonal center (key) of a Muse commit.
Key is the most fundamental property of a piece of music — knowing the key is a
prerequisite for harmonic generation, chord-scale selection, and tonal arc
analysis. An AI agent calls `muse key --json` before generating new material to
stay in the correct tonal center.

**Usage:**
```bash
muse key [<commit>] [OPTIONS]
```

**Flags:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `<commit>` | arg | HEAD | Commit SHA to analyse |
| `--set KEY` | str | — | Annotate with an explicit key (e.g. `"F# minor"`) |
| `--detect` | flag | on | Detect and display the key (default behaviour) |
| `--track TEXT` | str | — | Restrict key detection to a specific instrument track |
| `--relative` | flag | off | Show the relative key (e.g. `"Eb major / C minor"`) |
| `--history` | flag | off | Show how the key changed across all commits |
| `--json` | flag | off | Emit machine-readable JSON for agent consumption |

**Key format:** `<tonic> <mode>` — e.g. `"F# minor"`, `"Eb major"`. Valid tonics
include all 12 chromatic pitches with `#` and `b` enharmonics. Valid modes are
`major` and `minor`.

**Output example (text):**
```
Key: C major
Commit: a1b2c3d4  Branch: main
Track: all
(stub — full MIDI key detection pending)
```

**Output example (`--relative`):**
```
Key: A minor
Commit: a1b2c3d4  Branch: main
Track: all
Relative: C major
(stub — full MIDI key detection pending)
```

**Output example (`--json`):**
```json
{
  "key": "C major",
  "tonic": "C",
  "mode": "major",
  "relative": "",
  "commit": "a1b2c3d4",
  "branch": "main",
  "track": "all",
  "source": "stub"
}
```

**Output example (`--history --json`):**
```json
[
  {"commit": "a1b2c3d4", "key": "C major", "tonic": "C", "mode": "major", "source": "stub"}
]
```

**Result types:** `KeyDetectResult` (TypedDict) — fields: `key` (str), `tonic` (str),
`mode` (str), `relative` (str), `commit` (str), `branch` (str), `track` (str),
`source` (str). History mode returns `list[KeyHistoryEntry]`. See
`docs/reference/type_contracts.md § Muse CLI Types`.

**Agent use case:** Before generating a chord progression or melody, an agent runs
`muse key --json` to discover the tonal center of the most recent commit.
`muse key --history --json` reveals modulations across an album — if the key
changed from D major to F major at commit `abc123`, the agent knows a modulation
occurred and can generate transitional material accordingly.

**Implementation:** `maestro/muse_cli/commands/key.py` — `parse_key()`,
`relative_key()`, `_key_detect_async()`, `_key_history_async()`,
`_format_detect()`, `_format_history()`. Exit codes: 0 success, 1 invalid
`--set` value, 2 outside repo, 3 internal error.

> **Stub note:** Returns a placeholder key of `C major`. Full implementation
> requires chromatic pitch-class distribution analysis from committed MIDI note
> events (Krumhansl-Schmuckler or similar key-finding algorithm, future:
> Storpheus MIDI parse route).

---

## `muse ask` — Natural Language Query over Musical History

`muse ask "<question>"` searches Muse commit messages for keywords derived
from the user's question and returns matching commits in a structured answer.

**Purpose:** Give musicians and AI agents a conversational interface to
retrieve relevant moments from the composition history without remembering
exact commit messages or timestamps.

### Flags

| Flag | Default | Description |
|------|---------|-------------|
| `<question>` | *(required)* | Natural language question about your musical history. |
| `--branch <name>` | current HEAD branch | Restrict search to commits on this branch. |
| `--since YYYY-MM-DD` | *(none)* | Include only commits on or after this date. |
| `--until YYYY-MM-DD` | *(none)* | Include only commits on or before this date (inclusive, end-of-day). |
| `--json` | `false` | Emit machine-readable JSON instead of plain text. |
| `--cite` | `false` | Show full 64-character commit IDs instead of 8-character prefixes. |

### Output example

**Plain text:**

```
Based on Muse history (14 commits searched):
Commits matching your query: 2 found

  [a3f2c1b0] 2026-02-10 14:32  boom bap take 1
  [d9e8f7a6] 2026-02-11 09:15  boom bap take 2

Note: Full LLM-powered answer generation is a planned enhancement.
```

**JSON (`--json`):**

```json
{
  "question": "boom bap sessions",
  "total_searched": 14,
  "matches": [
    {
      "commit_id": "a3f2c1b0",
      "branch": "main",
      "message": "boom bap take 1",
      "committed_at": "2026-02-10T14:32:00+00:00"
    }
  ],
  "note": "Full LLM-powered answer generation is a planned enhancement."
}
```

### Result type

`muse ask` returns an `AnswerResult` object (see
`docs/reference/type_contracts.md § AnswerResult`). The `to_plain()` and
`to_json()` methods on `AnswerResult` render the two output formats.

### Agent use case

An AI agent reviewing a composition session calls `muse ask "piano intro" --json`
to retrieve all commits where piano intro work was recorded. The JSON output
feeds directly into the agent's context without screen-scraping, allowing it to
reference specific commit IDs when proposing the next variation.

The `--branch` filter lets an agent scope queries to a feature branch
(e.g., `feat/verse-2`) rather than searching across all experimental branches.
The `--cite` flag gives the agent full commit IDs for downstream `muse checkout`
or `muse log` calls.

**Implementation:** `maestro/muse_cli/commands/ask.py` — `_keywords()`,
`_ask_async()`, `AnswerResult`. Exit codes: 0 success, 2 outside repo,
3 internal error.

> **Stub note:** Keyword matching over commit messages only. Full LLM-powered
> semantic search (embedding similarity over commit content) is a planned
> enhancement (future: integrate with Qdrant vector store).

---

## `muse grep` — Search for a Musical Pattern Across All Commits

**Purpose:** Walk the full commit chain on the current branch and return every
commit whose message or branch name contains the given pattern.  Designed as
the textual precursor to full MIDI content search — the CLI contract (flags,
output modes, result type) is frozen now so agents can rely on it before the
deeper analysis is wired in.

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
| `PATTERN` | positional | — | Pattern to search (note sequence, interval, chord, or free text) |
| `--track TEXT` | string | — | [Future] Restrict to a named MIDI track |
| `--section TEXT` | string | — | [Future] Restrict to a labelled section |
| `--transposition-invariant / --no-transposition-invariant` | flag | on | [Future] Match regardless of key |
| `--rhythm-invariant` | flag | off | [Future] Match regardless of rhythm/timing |
| `--commits` | flag | off | Output one commit ID per line (like `git grep --name-only`) |
| `--json` | flag | off | Emit machine-readable JSON array |

**Output example (text):**
```
Pattern: 'pentatonic'  (1 match(es))

commit c1d2e3f4...
Branch:  feature/pentatonic-solo
Date:    2026-02-27T15:00:00+00:00
Match:   [message]
Message: add pentatonic riff to chorus
```

**Output example (`--commits`):**
```
c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2
```

**Output example (`--json`):**
```json
[
  {
    "commit_id": "c1d2e3f4...",
    "branch": "feature/pentatonic-solo",
    "message": "add pentatonic riff to chorus",
    "committed_at": "2026-02-27T15:00:00+00:00",
    "match_source": "message"
  }
]
```

**Result type:** `GrepMatch` (dataclass) — fields: `commit_id` (str),
`branch` (str), `message` (str), `committed_at` (str, ISO-8601),
`match_source` (str: `"message"` | `"branch"` | `"midi_content"`).
See `docs/reference/type_contracts.md § Muse CLI Types`.

**Agent use case:** An AI composing a variation searches previous commits for
all times "pentatonic" appeared in the history before deciding whether to
reuse or invert the motif.  The `--json` flag makes the result directly
parseable; `--commits` feeds a shell loop that checks out each matching
commit for deeper inspection.

**Implementation:** `maestro/muse_cli/commands/grep_cmd.py` —
`GrepMatch` (dataclass), `_load_all_commits()`, `_match_commit()`,
`_grep_async()`, `_render_matches()`.  Exit codes: 0 success,
2 outside repo, 3 internal error.

> **Stub note:** The current implementation matches commit *messages* and
> *branch names* only.  Full MIDI content search (note sequences, intervals,
> chord symbols, `--track`, `--section`, `--transposition-invariant`,
> `--rhythm-invariant`) is reserved for a future iteration.  Flags are accepted
> now to keep the CLI contract stable; supplying them emits a clear warning.

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
commits.

Auto-detected BPM is shown on the single-commit read path but is not persisted,
so it does not appear in history (history only reflects explicitly set annotations).

### MIDI Tempo Parsing

`maestro/services/muse_tempo.extract_bpm_from_midi(data: bytes)` is a pure
function that scans a raw MIDI byte string for the Set Tempo meta-event
(FF 51 03). The three bytes encode microseconds-per-beat as a 24-bit big-endian
integer. BPM = 60_000_000 / microseconds_per_beat. Only the first event is
returned; `detect_all_tempos_from_midi` returns all events (used for rubato
detection).

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
| `muse humanize` | `commands/humanize.py` | ✅ stub (PR #151) | #107 |
| `muse describe` | `commands/describe.py` | ✅ stub (PR #134) | #125 |
| `muse ask` | `commands/ask.py` | ✅ stub (PR #132) | #126 |
| `muse session` | `commands/session.py` | ✅ implemented (PR #129) | #127 |
| `muse tempo` | `commands/tempo.py` | ✅ fully implemented (PR TBD) | #116 |

All stub commands have stable CLI contracts. Full musical analysis (MIDI content
parsing, vector embeddings, LLM synthesis) is tracked as follow-up issues.

## `muse recall` — Keyword Search over Musical Commit History

**Purpose:** Walk the commit history on the current (or specified) branch and
return the top-N commits ranked by keyword overlap against their commit
messages.  Designed as the textual precursor to full vector embedding search —
the CLI interface (flags, output modes, result type) is frozen so agents can
rely on it before Qdrant-backed semantic search is wired in.

**Usage:**
```bash
muse recall <query> [OPTIONS]
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
| `QUERY` | positional | — | Natural-language description to search for |
| `--limit / -n INT` | integer | 5 | Maximum number of results to return |
| `--threshold FLOAT` | float | 0.6 | Minimum keyword-overlap score (0–1) to include a commit |
| `--branch TEXT` | string | current branch | Filter to a specific branch name |
| `--since YYYY-MM-DD` | date string | — | Only include commits on or after this date |
| `--until YYYY-MM-DD` | date string | — | Only include commits on or before this date |
| `--json` | flag | off | Emit machine-readable JSON array |

**Scoring algorithm:** Overlap coefficient — `|Q ∩ M| / |Q|` — where Q is the
set of lowercase word tokens in the query and M is the set of tokens in the
commit message.  A score of 1.0 means every query word appears in the message;
0.0 means none do.  Commits with score below `--threshold` are excluded.

**Output example (text):**
```
Recall: "dark jazz bassline"
(keyword match · threshold 0.60 · vector search is a planned enhancement)

  #1  score=1.0000  commit a1b2c3d4...  [2026-02-20 14:30:00]
       add dark jazz bassline to verse

  #2  score=0.6667  commit e5f6a7b8...  [2026-02-18 09:15:00]
       jazz bassline variation with reverb
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

## `muse meter` — Time Signature Read/Set/Detect

### `muse meter`

**Purpose:** Read or set the time signature (meter) annotation for any commit. The
time signature defines the rhythmic framework of a piece — a shift from 4/4 to 7/8 is
a fundamental compositional decision. `muse meter` makes that history first-class.

**Status:** ✅ Fully implemented (issue #117)

**Storage:** The time signature is stored as the `meter` key inside the nullable
`extra_metadata` JSON column on `muse_cli_commits`. No MIDI file is modified. The
annotation is layered on top of the immutable content-addressed snapshot.

**Time signature format:** `<numerator>/<denominator>` where the denominator must be a
power of 2. Examples: `4/4`, `3/4`, `7/8`, `5/4`, `12/8`, `6/8`.

#### Flags

| Flag | Argument | Description |
|------|----------|-------------|
| *(none)* | `[COMMIT]` | Read the stored time signature. Default: HEAD. |
| `--set` | `TIME_SIG` | Store a time signature annotation on the commit. |
| `--detect` | — | Auto-detect from MIDI time-signature meta events in `muse-work/`. |
| `--history` | — | Walk the branch and show when the time signature changed. |
| `--polyrhythm` | — | Detect tracks with conflicting time signatures in `muse-work/`. |

#### Examples

```bash
# Read the stored time signature for HEAD
muse meter

# Read the time signature for a specific commit (abbreviated SHA)
muse meter a1b2c3d4

# Set the time signature on HEAD
muse meter --set 7/8

# Set the time signature on a specific commit
muse meter a1b2c3d4 --set 5/4

# Auto-detect from MIDI files and store the result
muse meter --detect

# Show time signature history (newest-first, with change markers)
muse meter --history

# Check for polyrhythmic tracks
muse meter --polyrhythm
```

#### Sample output

**Read (no flag):**
```
commit a1b2c3d4
meter  7/8
```

**History (`--history`):**
```
a1b2c3d4  7/8           switched to odd meter        ← changed
f9e8d7c6  4/4           boom bap demo take 1
e7d6c5b4  4/4           initial take
```

**Polyrhythm (`--polyrhythm`, conflict detected):**
```
⚠️  Polyrhythm detected — multiple time signatures in this commit:

  4/4           tracks/drums.mid
  7/8           tracks/melody.mid
```

#### MIDI Detection

`--detect` scans `.mid` and `.midi` files in `muse-work/` for MIDI time-signature
meta events (type `0xFF 0x58`). The event layout is:

```
FF 58 04  nn dd cc bb
          │  │  │  └── 32nd notes per 24 MIDI clocks
          │  │  └───── MIDI clocks per metronome tick
          │  └──────── denominator exponent (denominator = 2^dd)
          └─────────── numerator
```

The most common signature across all files is selected and written to the commit.
Files with no time-signature event report `?` and are excluded from polyrhythm
detection (only known signatures are compared).

#### Result types

| Type | Module | Description |
|------|--------|-------------|
| `MuseMeterReadResult` | `maestro/muse_cli/commands/meter.py` | Commit ID + stored time signature (or `None`) |
| `MuseMeterHistoryEntry` | `maestro/muse_cli/commands/meter.py` | Single entry in the parent-chain meter walk |
| `MusePolyrhythmResult` | `maestro/muse_cli/commands/meter.py` | Per-file time signatures + polyrhythm flag |

**Agent use case:** An AI generating a new section calls `muse meter` to discover whether
the project is in 4/4 or an odd meter before producing MIDI. An agent reviewing a composition
calls `muse meter --history` to identify when meter changes occurred and correlate them with
creative decisions. `muse meter --polyrhythm` surfaces conflicts that would cause tracks to
drift out of sync.

**Implementation:** `maestro/muse_cli/commands/meter.py`. All DB-touching paths are async
(`open_session()` pattern). Exit codes: 0 success, 1 user error, 3 internal error.

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
| `muse meter` | `commands/meter.py` | ✅ implemented (PR #141) | #117 |

All stub commands have stable CLI contracts. Full musical analysis (MIDI content
parsing, vector embeddings, LLM synthesis) is tracked as follow-up issues.
[
  {
    "rank": 1,
    "score": 1.0,
    "commit_id": "a1b2c3d4...",
    "date": "2026-02-20 14:30:00",
    "branch": "main",
    "message": "add dark jazz bassline to verse"
  }
]
```

**Result type:** `RecallResult` (`TypedDict`) — fields: `rank` (int),
`score` (float, rounded to 4 decimal places), `commit_id` (str),
`date` (str, `"YYYY-MM-DD HH:MM:SS"`), `branch` (str), `message` (str).
See `docs/reference/type_contracts.md § RecallResult`.

**Agent use case:** An AI composing a new variation queries `muse recall
"dark jazz bassline"` to surface all commits that previously explored that
texture — letting the agent reuse, invert, or contrast those ideas.  The
`--json` flag makes the result directly parseable in an agentic pipeline;
`--threshold 0.0` with a broad query retrieves the full ranked history.

**Implementation:** `maestro/muse_cli/commands/recall.py` —
`RecallResult` (TypedDict), `_tokenize()`, `_score()`, `_fetch_commits()`,
`_recall_async()`, `_render_results()`.  Exit codes: 0 success,
1 bad date format (`USER_ERROR`), 2 outside repo (`REPO_NOT_FOUND`),
3 internal error (`INTERNAL_ERROR`).

> **Planned enhancement:** Full semantic vector search via Qdrant with
> cosine similarity over pre-computed embeddings.  When implemented, the
> scoring function will be replaced with no change to the CLI interface.

---
## `muse context` — Structured Musical Context for AI Agents

**Purpose:** Output a structured, self-contained musical context document for AI agent consumption. This is the **primary interface between Muse VCS and AI music generation agents** — agents run `muse context` before any generation task to understand the current key, tempo, active tracks, form, harmonic profile, and evolutionary history of the composition.

**Usage:**
```bash
muse context [<commit>] [OPTIONS]
```

**Flags:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `<commit>` | positional | HEAD | Target commit ID to inspect |
| `--depth N` | int | 5 | Number of ancestor commits to include in `history` |
| `--sections` | flag | off | Expand section-level detail in `musical_state.sections` |
| `--tracks` | flag | off | Add per-track harmonic and dynamic breakdowns |
| `--include-history` | flag | off | Annotate history entries with dimensional deltas (future Storpheus integration) |
| `--format json\|yaml` | string | json | Output format |

**Output example (`--format json`):**
```json
{
  "repo_id": "a1b2c3d4-...",
  "current_branch": "main",
  "head_commit": {
    "commit_id": "abc1234...",
    "message": "Add piano melody to verse",
    "author": "Gabriel",
    "committed_at": "2026-02-27T22:00:00+00:00"
  },
  "musical_state": {
    "active_tracks": ["bass", "drums", "piano"],
    "key": null,
    "tempo_bpm": null,
    "sections": null,
    "tracks": null
  },
  "history": [
    {
      "commit_id": "...",
      "message": "Add bass line",
      "active_tracks": ["bass", "drums"],
      "key": null,
      "tempo_bpm": null
    }
  ],
  "missing_elements": [],
  "suggestions": {}
}
```

**Result type:** `MuseContextResult` — fields: `repo_id`, `current_branch`, `head_commit` (`MuseHeadCommitInfo`), `musical_state` (`MuseMusicalState`), `history` (`list[MuseHistoryEntry]`), `missing_elements`, `suggestions`. See `docs/reference/type_contracts.md`.

**Agent use case:** When Maestro receives a "generate a new section" request, it runs `muse context --format json` to obtain the current musical state, passes the result to the LLM, and the LLM generates music that is harmonically, rhythmically, and structurally coherent with the existing composition. Without this command, generation decisions are musically incoherent.

**Implementation notes:**
- `active_tracks` is populated from MIDI/audio file names in the snapshot manifest (real data).
- Musical dimensions (`key`, `tempo_bpm`, `form`, `emotion`, harmonic/dynamic/melodic profiles) are `null` until Storpheus MIDI analysis is integrated. The full schema is defined and stable.
- `sections` and `tracks` are populated when the respective flags are passed; sections currently use a single "main" stub section containing all active tracks until MIDI region metadata is available.
- Output is **deterministic**: for the same `commit_id` and flags, the output is always identical.

**Implementation:** `maestro/services/muse_context.py` (service layer), `maestro/muse_cli/commands/context.py` (CLI command). Exit codes: 0 success, 1 user error (bad commit, no commits), 2 outside repo, 3 internal.

---

## `muse dynamics` — Dynamic (Velocity) Profile Analysis

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

## `muse humanize` — Apply Micro-Timing and Velocity Humanization to Quantized MIDI

**Purpose:** Apply realistic human-performance variation to machine-quantized MIDI, producing a new Muse commit that sounds natural. AI agents use this after generating quantized output to make compositions feel human before presenting them to DAW users.

**Usage:**
```bash
muse humanize [COMMIT] [OPTIONS]
```

**Flags:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `COMMIT` | argument | HEAD | Source commit ref to humanize |
| `--tight` | flag | off | Subtle: timing +/-5 ms, velocity +/-5 |
| `--natural` | flag | on | Moderate: timing +/-12 ms, velocity +/-10 (default) |
| `--loose` | flag | off | Heavy: timing +/-20 ms, velocity +/-15 |
| `--factor FLOAT` | float | - | Custom factor 0.0-1.0 (overrides preset) |
| `--timing-only` | flag | off | Apply timing variation only; preserve velocities |
| `--velocity-only` | flag | off | Apply velocity variation only; preserve timing |
| `--track TEXT` | string | all | Restrict to one track (prefix match) |
| `--section TEXT` | string | all | Restrict to a named section |
| `--seed N` | int | - | Fix random seed for reproducible output |
| `--message TEXT` | string | auto | Commit message |
| `--json` | flag | off | Emit structured JSON for agent consumption |

**Result types:** `HumanizeResult` and `TrackHumanizeResult` (both TypedDict). See `docs/reference/type_contracts.md`.

**Agent use case:** After `muse commit` records a machine-generated MIDI variation, an AI agent runs `muse humanize --natural --seed 42` to add realistic performance feel. Drum groove is preserved automatically (GM channel 10 excluded from timing variation).

**Implementation:** `maestro/muse_cli/commands/humanize.py`. Exit codes: 0 success, 1 flag conflict, 2 outside repo, 3 internal.

> **Stub note:** Full MIDI note rewrite pending Storpheus note-level access. CLI interface is stable.

---

## `muse import` — Import a MIDI or MusicXML File as a New Muse Commit

### Overview

`muse import <file>` ingests an external music file into a Muse-tracked project
by copying it into `muse-work/imports/` and creating a Muse commit.  It is the
primary on-ramp for bringing existing DAW sessions, MIDI exports, or orchestral
scores under Muse version control.

### Supported Formats

| Extension | Format | Parser |
|-----------|--------|--------|
| `.mid`, `.midi` | Standard MIDI File | `mido` library |
| `.xml`, `.musicxml` | MusicXML (score-partwise) | `xml.etree.ElementTree` |

### Command Signature

```
muse import <file> [OPTIONS]

Arguments:
  file          Path to the MIDI or MusicXML file to import.

Options:
  --message, -m TEXT   Commit message (default: "Import <filename>").
  --track-map TEXT     Map MIDI channels to track names.
                       Format: "ch0=bass,ch1=piano,ch9=drums"
  --section TEXT       Tag the imported content as a specific section.
  --analyze            Run multi-dimensional analysis and display results.
  --dry-run            Validate only — do not write files or commit.
```

### What It Does

1. **Validate** — Checks that the file extension is supported.  Clear error on unsupported types.
2. **Parse** — Extracts `NoteEvent` objects (pitch, velocity, timing, channel) using format-specific parsers.
3. **Apply track map** — Renames `channel_name` fields for any channels listed in `--track-map`.
4. **Copy** — Copies the source file to `muse-work/imports/<filename>`.
5. **Write metadata** — Creates `muse-work/imports/<filename>.meta.json` with note count, tracks, tempo, and track-map.
6. **Commit** — Calls `_commit_async` to create a Muse commit with the imported content.
7. **Analyse (optional)** — Prints a three-dimensional analysis: harmonic (pitch range, top pitches), rhythmic (note count, density, beats), dynamic (velocity distribution).

### Track Map Syntax

The `--track-map` option accepts a comma-separated list of `KEY=VALUE` pairs where
KEY is either `ch<N>` (e.g. `ch0`) or a bare channel number (e.g. `0`):

```
muse import song.mid --track-map "ch0=bass,ch1=piano,ch9=drums"
```

Unmapped channels retain their default label `ch<N>`.  The mapping is persisted
in `muse-work/imports/<filename>.meta.json` so downstream tooling can reconstruct
track assignments from a commit.

### Metadata JSON Format

Every import writes a sidecar JSON file alongside the imported file:

```json
{
  "source": "/absolute/path/to/source.mid",
  "format": "midi",
  "ticks_per_beat": 480,
  "tempo_bpm": 120.0,
  "note_count": 64,
  "tracks": ["bass", "piano", "drums"],
  "track_map": {"ch0": "bass", "ch1": "piano", "ch9": "drums"},
  "section": "verse",
  "raw_meta": {"num_tracks": 3}
}
```

### Dry Run

`--dry-run` validates the file and shows what would be committed without creating
any files or DB rows:

```
$ muse import song.mid --dry-run
✅ Dry run: 'song.mid' is valid (midi)
   Notes: 128, Tracks: 3, Tempo: 120.0 BPM
   Would commit: "Import song.mid"
```

### Analysis Output

`--analyze` appends a three-section report after the import:

```
Analysis:
  Format:      midi
  Tempo:       120.0 BPM
  Tracks:      bass, piano, drums

  ── Harmonic ──────────────────────────────────
  Pitch range: C2–G5
  Top pitches: E4(12x), C4(10x), G4(8x), D4(6x), A4(5x)

  ── Rhythmic ──────────────────────────────────
  Notes:       128
  Span:        32.0 beats
  Density:     4.0 notes/beat

  ── Dynamic ───────────────────────────────────
  Velocity:    avg=82, min=64, max=110
  Character:   f (loud)
```

### Implementation

| File | Role |
|------|------|
| `maestro/muse_cli/midi_parser.py` | Parsing, track-map, analysis — all pure functions, no DB or I/O |
| `maestro/muse_cli/commands/import_cmd.py` | Typer command and `_import_async` core |
| `tests/muse_cli/test_import.py` | 23 unit + integration tests |

### Muse VCS Considerations

- **Affected operation:** `commit` — creates a new commit row.
- **Postgres state:** One new `muse_cli_commits` row, one `muse_cli_snapshots` row, and two `muse_cli_objects` rows (the MIDI/XML file + the `.meta.json`).
- **No schema migration required** — uses existing tables.
- **Reproducibility:** Deterministic — same file + same flags → identical commit content (same `snapshot_id`).
- **`muse-work/imports/`** — the canonical import landing zone, parallel to `muse-work/tracks/`, `muse-work/renders/`, etc.

### Error Handling

| Scenario | Exit code | Message |
|----------|-----------|---------|
| File not found | 1 (USER_ERROR) | `❌ File not found: <path>` |
| Unsupported extension | 1 (USER_ERROR) | `❌ Unsupported file extension '.<ext>'. Supported: …` |
| Malformed MIDI | 1 (USER_ERROR) | `❌ Cannot parse MIDI file '<path>': <reason>` |
| Malformed MusicXML | 1 (USER_ERROR) | `❌ Cannot parse MusicXML file '<path>': <reason>` |
| Invalid `--track-map` | 1 (USER_ERROR) | `❌ --track-map: Invalid track-map entry …` |
| Not in a repo | 2 (REPO_NOT_FOUND) | Standard `require_repo()` message |
| Unexpected failure | 3 (INTERNAL_ERROR) | `❌ muse import failed: <exc>` |

---

## `muse divergence` — Musical Divergence Between Two Branches

**Purpose:** Show how two branches have diverged *musically* — useful when two
producers are working on different arrangements of the same project and you need
to understand the creative distance before deciding which to merge.

**Implementation:** `maestro/muse_cli/commands/divergence.py`\
**Service:** `maestro/services/muse_divergence.py`\
**Status:** ✅ implemented (issue #119)

### Flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `BRANCH_A` | positional | required | First branch name |
| `BRANCH_B` | positional | required | Second branch name |
| `--since COMMIT` | string | auto | Common ancestor commit ID (auto-detected via merge-base BFS if omitted) |
| `--dimensions TEXT` | string (repeatable) | all five | Musical dimension(s) to analyse |
| `--json` | flag | off | Machine-readable JSON output |

### What It Computes

1. **Finds the merge base** — BFS over `MuseCliCommit.parent_commit_id` / `parent2_commit_id`, equivalent to `git merge-base`.
2. **Collects changed paths** — diff from merge-base snapshot to branch-tip (added + deleted + modified paths).
3. **Classifies paths by dimension** — keyword matching on lowercase filename.
4. **Scores each dimension** — `score = |sym_diff(A, B)| / |union(A, B)|`.  0.0 = identical; 1.0 = completely diverged.
5. **Classifies level** — `NONE` (<0.15), `LOW` (0.15–0.40), `MED` (0.40–0.70), `HIGH` (≥0.70).
6. **Computes overall score** — mean of per-dimension scores.

### Result types

`DivergenceLevel` (Enum), `DimensionDivergence` (frozen dataclass), `MuseDivergenceResult` (frozen dataclass).
See `docs/reference/type_contracts.md § Muse Divergence Types`.

### Agent use case

An AI deciding which branch to merge calls `muse divergence feature/guitar feature/piano --json`
before generation.  HIGH harmonic divergence + LOW rhythmic divergence means lean on the piano
branch for chord voicings while preserving the guitar branch's groove patterns.

### `muse timeline`

**Purpose:** Render a commit-by-commit chronological view of a composition's
creative arc — emotion transitions, section progress, and per-track activity.
This is the "album liner notes" view that no Git command provides.  Agents
use it to understand how a project's emotional and structural character
evolved before making generation decisions.

**Usage:**
```bash
muse timeline [RANGE] [OPTIONS]
```

**Flags:**
| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `RANGE` | positional string | full history | Commit range (reserved — full history shown for now) |
| `--emotion` | flag | off | Add emotion column (from `emotion:*` tags) |
| `--sections` | flag | off | Group commits under section headers (from `section:*` tags) |
| `--tracks` | flag | off | Show per-track activity column (from `track:*` tags) |
| `--json` | flag | off | Emit structured JSON for UI rendering or agent consumption |
| `--limit N` | int | 1000 | Maximum commits to walk |

**Output example (text):**
```
Timeline — branch: main  (3 commit(s))

  ── verse ──
2026-02-01  abc1234  Initial drum arrangement    [drums]        [melancholic]  ████
2026-02-02  def5678  Add bass line               [bass]         [melancholic]  ██████
  ── chorus ──
2026-02-03  ghi9012  Chorus melody               [keys,vocals]  [joyful]       █████████

Emotion arc: melancholic → joyful
Sections:    verse → chorus
```

**Output example (JSON):**
```json
{
  "branch": "main",
  "total_commits": 3,
  "emotion_arc": ["melancholic", "joyful"],
  "section_order": ["verse", "chorus"],
  "entries": [
    {
      "commit_id": "abc1234...",
      "short_id": "abc1234",
      "committed_at": "2026-02-01T00:00:00+00:00",
      "message": "Initial drum arrangement",
      "emotion": "melancholic",
      "sections": ["verse"],
      "tracks": ["drums"],
      "activity": 1
    }
  ]
}
```

**Result types:** `MuseTimelineEntry`, `MuseTimelineResult` — see `docs/reference/type_contracts.md § Muse Timeline Types`.

**Agent use case:** An AI agent calls `muse timeline --json` before composing a new
section to understand the emotional arc to date (e.g. `melancholic → joyful → tense`).
It uses `section_order` to determine what structural elements have been established
and `emotion_arc` to decide whether to maintain or contrast the current emotional
character.  `activity` per commit helps identify which sections were most actively
developed.

**Implementation note:** Emotion, section, and track data are derived entirely from
tags attached via `muse tag add`.  Commits with no tags show `—` in filtered columns.
The commit range argument (`RANGE`) is accepted but reserved for a future iteration
that supports `HEAD~10..HEAD` syntax.

---

### `muse validate`

**Purpose:** Run integrity checks against the working tree before `muse commit`.
Detects corrupted MIDI files, manifest mismatches, duplicate instrument roles,
non-conformant section names, and unknown emotion tags — giving agents and
producers an actionable quality gate before bad state enters history.

**Status:** ✅ Fully implemented (issue #99)

**Usage:**
```bash
muse validate [OPTIONS]
```

**Flags:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--strict` | flag | off | Exit 2 on warnings as well as errors. |
| `--track TEXT` | string | — | Restrict checks to files/paths containing TEXT (case-insensitive). |
| `--section TEXT` | string | — | Restrict section-naming check to directories containing TEXT. |
| `--fix` | flag | off | Auto-fix correctable issues (conservative; no data-loss risk). |
| `--json` | flag | off | Emit full structured JSON for agent consumption. |

**Exit codes:**

| Code | Meaning |
|------|---------|
| 0 | All checks passed — working tree is clean. |
| 1 | One or more ERROR issues found (corrupted MIDI, orphaned files). |
| 2 | WARN issues found AND `--strict` was passed. |
| 3 | Internal error (unexpected exception). |

**Checks performed:**

| Check | Severity | Description |
|-------|----------|-------------|
| `midi_integrity` | ERROR | Verifies each `.mid`/`.midi` has a valid SMF `MThd` header. |
| `manifest_consistency` | ERROR/WARN | Compares committed snapshot manifest vs actual working tree. |
| `no_duplicate_tracks` | WARN | Detects multiple MIDI files sharing the same instrument role. |
| `section_naming` | WARN | Verifies section dirs match `[a-z][a-z0-9_-]*`. |
| `emotion_tags` | WARN | Checks emotion tags (`.muse/tags.json`) against the allowed vocabulary. |

**Output example (human-readable):**
```
Validating working tree …

  ✅ midi_integrity              PASS
  ❌ manifest_consistency        FAIL
       ❌ ERROR   beat.mid  File in committed manifest is missing from working tree.
  ✅ no_duplicate_tracks         PASS
  ⚠️  section_naming             WARN
       ⚠️  WARN   Verse  Section directory 'Verse' does not follow naming convention.
  ✅ emotion_tags                PASS

⚠️  1 error, 1 warning — working tree has integrity issues.
```

**Output example (`--json`):**
```json
{
  "clean": false,
  "has_errors": true,
  "has_warnings": true,
  "checks": [
    { "name": "midi_integrity", "passed": true, "issues": [] },
    {
      "name": "manifest_consistency",
      "passed": false,
      "issues": [
        {
          "severity": "error",
          "check": "manifest_consistency",
          "path": "beat.mid",
          "message": "File in committed manifest is missing from working tree (orphaned)."
        }
      ]
    }
  ],
  "fixes_applied": []
}
```

**Result types:** `MuseValidateResult`, `ValidationCheckResult`, `ValidationIssue`, `ValidationSeverity`
— all defined in `maestro/services/muse_validate.py` and registered in `docs/reference/type_contracts.md`.

**Agent use case:** An AI composition agent calls `muse validate --json` before every
`muse commit` to confirm the working tree is consistent. If `has_errors` is true the agent
must investigate the failing check before committing — a corrupted MIDI would silently
corrupt the composition history. With `--strict`, agents can enforce zero-warning quality gates.

---
## `muse diff` — Music-Dimension Diff Between Commits

**Purpose:** Compare two commits across five orthogonal musical dimensions —
harmonic, rhythmic, melodic, structural, and dynamic.  Where `git diff` tells
you "file changed," `muse diff --harmonic` tells you "the song modulated from
Eb major to F minor and the tension profile doubled."  This is the killer
feature that proves Muse's value over Git: musically meaningful version control.

**Usage:**
```bash
muse diff [<COMMIT_A>] [<COMMIT_B>] [OPTIONS]
```

Defaults: `COMMIT_A` = HEAD~1, `COMMIT_B` = HEAD.

**Flags:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `COMMIT_A` | positional | HEAD~1 | Earlier commit ref |
| `COMMIT_B` | positional | HEAD | Later commit ref |
| `--harmonic` | flag | off | Compare key, mode, chord progression, tension |
| `--rhythmic` | flag | off | Compare tempo, meter, swing, groove drift |
| `--melodic` | flag | off | Compare motifs, contour, pitch range |
| `--structural` | flag | off | Compare sections, instrumentation, form |
| `--dynamic` | flag | off | Compare velocity arc, per-track loudness |
| `--all` | flag | off | Run all five dimensions simultaneously |
| `--json` | flag | off | Emit structured JSON for agent consumption |

**Output example (`muse diff HEAD~1 HEAD --harmonic`):**
```
Harmonic diff: abc1234 -> def5678

Key:           Eb major -> F minor
Mode:          Major -> Minor
Chord prog:    I-IV-V-I -> i-VI-III-VII
Tension:       Low (0.2) -> Medium-High (0.65)
Summary:       Major harmonic restructuring — key modulation down a minor 3rd, shift to Andalusian cadence
```

**Output example (`muse diff HEAD~1 HEAD --rhythmic`):**
```
Rhythmic diff: abc1234 -> def5678

Tempo:         120.0 BPM -> 128.0 BPM (+8.0 BPM)
Meter:         4/4 -> 4/4
Swing:         Straight (0.5) -> Light swing (0.57)
Groove drift:  12.0ms -> 6.0ms
Summary:       Slightly faster, more swung, tighter quantization
```

**Output example (`muse diff HEAD~1 HEAD --all`):**
```
Music diff: abc1234 -> def5678
Changed:   harmonic, rhythmic, melodic, structural, dynamic
Unchanged: (none)

-- Harmonic --
...

-- Rhythmic --
...
```

**Unchanged dimensions:** When a dimension shows no change, the renderer appends
`Unchanged` to the block rather than omitting it.  This guarantees agents always
receive a complete report — silence is never ambiguous.

**Result types:**

| Type | Fields |
|------|--------|
| `HarmonicDiffResult` | `commit_a/b`, `key_a/b`, `mode_a/b`, `chord_prog_a/b`, `tension_a/b`, `tension_label_a/b`, `summary`, `changed` |
| `RhythmicDiffResult` | `commit_a/b`, `tempo_a/b`, `meter_a/b`, `swing_a/b`, `swing_label_a/b`, `groove_drift_ms_a/b`, `summary`, `changed` |
| `MelodicDiffResult` | `commit_a/b`, `motifs_introduced`, `motifs_removed`, `contour_a/b`, `range_low_a/b`, `range_high_a/b`, `summary`, `changed` |
| `StructuralDiffResult` | `commit_a/b`, `sections_added`, `sections_removed`, `instruments_added`, `instruments_removed`, `form_a/b`, `summary`, `changed` |
| `DynamicDiffResult` | `commit_a/b`, `avg_velocity_a/b`, `arc_a/b`, `tracks_louder`, `tracks_softer`, `tracks_silent`, `summary`, `changed` |
| `MusicDiffReport` | All five dimension results + `changed_dimensions`, `unchanged_dimensions`, `summary` |

See `docs/reference/type_contracts.md § Muse Diff Types`.

**Agent use case:** An AI composing a new variation runs
`muse diff HEAD~3 HEAD --harmonic --json` before generating to understand
whether the last three sessions have been converging on a key or exploring
multiple tonalities.  The `changed_dimensions` field in `MusicDiffReport` lets
the agent prioritize which musical parameters to vary next.

**Implementation:** `maestro/muse_cli/commands/diff.py` —
`HarmonicDiffResult`, `RhythmicDiffResult`, `MelodicDiffResult`,
`StructuralDiffResult`, `DynamicDiffResult`, `MusicDiffReport` (TypedDicts);
`_harmonic_diff_async()`, `_rhythmic_diff_async()`, `_melodic_diff_async()`,
`_structural_diff_async()`, `_dynamic_diff_async()`, `_diff_all_async()`;
`_render_harmonic()`, `_render_rhythmic()`, `_render_melodic()`,
`_render_structural()`, `_render_dynamic()`, `_render_report()`;
`_resolve_refs()`, `_tension_label()`.
Exit codes: 0 success, 2 outside repo (`REPO_NOT_FOUND`), 3 internal error.

> **Stub note:** All dimension analyses return realistic placeholder data.
> Full implementation requires Storpheus MIDI parsing for chord/tempo/motif
> extraction.  The CLI contract (flags, output schema, result types) is frozen
> so agents can rely on it before the analysis pipeline is wired in.

---

## `muse inspect` — Print Structured JSON of the Muse Commit Graph

**Purpose:** Serialize the full commit graph reachable from a starting reference
into machine-readable output.  This is the primary introspection tool for AI
agents and tooling that need to programmatically traverse or audit commit history,
branch state, and compositional metadata without parsing human-readable output.

**Implementation:** `maestro/muse_cli/commands/inspect.py`\
**Service:** `maestro/services/muse_inspect.py`\
**Status:** ✅ implemented (issue #98)

### Usage

```bash
muse inspect                          # JSON of HEAD branch history
muse inspect abc1234                  # start from a specific commit
muse inspect --depth 5                # limit to 5 commits
muse inspect --branches               # include all branch heads
muse inspect --format dot             # Graphviz DOT graph
muse inspect --format mermaid         # Mermaid.js graph definition
```

### Flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `[<ref>]` | positional | HEAD | Starting commit ID or branch name |
| `--depth N` | int | unlimited | Limit graph traversal to N commits per branch |
| `--branches` | flag | off | Include all branch heads and their reachable commits |
| `--tags` | flag | off | Include tag refs in the output |
| `--format` | enum | `json` | Output format: `json`, `dot`, `mermaid` |

### Output example (JSON)

```json
{
  "repo_id": "550e8400-e29b-41d4-a716-446655440000",
  "current_branch": "main",
  "branches": {
    "main": "a1b2c3d4e5f6...",
    "feature/guitar": "f9e8d7c6b5a4..."
  },
  "commits": [
    {
      "commit_id": "a1b2c3d4e5f6...",
      "short_id": "a1b2c3d4",
      "branch": "main",
      "parent_commit_id": "f9e8d7c6b5a4...",
      "parent2_commit_id": null,
      "message": "boom bap demo take 2",
      "author": "",
      "committed_at": "2026-02-27T17:30:00+00:00",
      "snapshot_id": "deadbeef...",
      "metadata": {"tempo_bpm": 95.0},
      "tags": ["emotion:melancholic", "stage:rough-mix"]
    }
  ]
}
```

### Result types

`MuseInspectCommit` (frozen dataclass) — one commit node in the graph.\
`MuseInspectResult` (frozen dataclass) — full serialized graph with branch pointers.\
`InspectFormat` (str Enum) — `json`, `dot`, `mermaid`.\
See `docs/reference/type_contracts.md § Muse Inspect Types`.

### Format: DOT

Graphviz DOT directed graph.  Pipe to `dot -Tsvg` to render a visual DAG:

```bash
muse inspect --format dot | dot -Tsvg -o graph.svg
```

Each commit becomes an ellipse node labelled `<short_id>\n<message[:40]>`.
Parent edges point child → parent (matching git convention).  Branch refs
appear as bold rectangle nodes pointing to their HEAD commit.

### Format: Mermaid

Mermaid.js `graph LR` definition.  Embed in GitHub markdown:

```
muse inspect --format mermaid
```

```mermaid
graph LR
  a1b2c3d4["a1b2c3d4: boom bap demo take 2"]
  f9e8d7c6["f9e8d7c6: boom bap demo take 1"]
  a1b2c3d4 --> f9e8d7c6
  main["main"]
  main --> a1b2c3d4
```

### Agent use case

An AI composition agent calls `muse inspect --format json` before generating
new music to understand the full lineage of the project:

1. **Branch discovery** — which creative threads exist (`branches` dict).
2. **Graph traversal** — which commits are ancestors, which are on feature branches.
3. **Metadata audit** — which commits have explicit tempo, meter, or emotion tags.
4. **Divergence awareness** — combined with `muse divergence`, informs merge decisions.

The JSON output is deterministic for a fixed graph state, making it safe to cache
between agent invocations and diff to detect graph changes.

---

## `muse render-preview [<commit>]` — Audio Preview of a Commit Snapshot

**Purpose:** Render the MIDI snapshot of any commit to an audio file, letting producers and AI agents hear what the project sounded like at any point in history — without opening a DAW session.  The musical equivalent of `git show <commit>` with audio playback.

**Implementation:** `maestro/muse_cli/commands/render_preview.py`\
**Service:** `maestro/services/muse_render_preview.py`\
**Status:** ✅ implemented (issue #96)

### Flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `[<commit>]` | positional string | HEAD | Short commit ID prefix to preview |
| `--format` / `-f` | `wav\|mp3\|flac` | `wav` | Output audio format |
| `--track TEXT` | string | all | Render only MIDI files matching this track name substring |
| `--section TEXT` | string | all | Render only MIDI files matching this section name substring |
| `--output` / `-o` | path | `/tmp/muse-preview-<short_id>.<fmt>` | Write the preview to this path |
| `--open` | flag | off | Open the rendered preview in the system default audio player (macOS only) |
| `--json` | flag | off | Emit structured JSON for agent consumption |

### Output example (text mode)

```
⚠️  Preview generated (stub — Storpheus /render not yet deployed):
   /tmp/muse-preview-abc12345.wav
   (1 MIDI files used)
```

### JSON output example (`--json`)

```json
{
  "commit_id": "abc12345def67890...",
  "commit_short": "abc12345",
  "output_path": "/tmp/muse-preview-abc12345.wav",
  "format": "wav",
  "midi_files_used": 1,
  "skipped_count": 0,
  "stubbed": true
}
```

### Result type: `RenderPreviewResult`

Defined in `maestro/services/muse_render_preview.py`.

| Field | Type | Description |
|-------|------|-------------|
| `output_path` | `pathlib.Path` | Absolute path of the rendered audio file |
| `format` | `PreviewFormat` | Audio format enum (`wav` / `mp3` / `flac`) |
| `commit_id` | `str` | Full commit ID (64-char SHA) |
| `midi_files_used` | `int` | Number of MIDI files from the snapshot used |
| `skipped_count` | `int` | Manifest entries skipped (wrong type / filter / missing) |
| `stubbed` | `bool` | `True` when Storpheus `/render` is not yet deployed and the file is a MIDI placeholder |

### Error handling

| Scenario | Exit code | Message |
|----------|-----------|---------|
| Not in a Muse repo | 2 (REPO_NOT_FOUND) | Standard `require_repo()` message |
| No commits yet | 1 (USER_ERROR) | `❌ No commits yet — nothing to export.` |
| Ambiguous commit prefix | 1 (USER_ERROR) | Lists all matching commits |
| No MIDI files after filter | 1 (USER_ERROR) | `❌ No MIDI files found in snapshot…` |
| Storpheus unreachable | 3 (INTERNAL_ERROR) | `❌ Storpheus not reachable — render aborted.` |

### Storpheus render status

The Storpheus service currently exposes MIDI *generation* at `POST /generate`.  A dedicated `POST /render` endpoint (MIDI-in → audio-out) is planned but not yet deployed.  Until it ships:

- A health-check confirms Storpheus is reachable (fast probe, 3 s timeout).
- The first matching MIDI file from the snapshot is **copied** to `output_path` as a placeholder.
- `RenderPreviewResult.stubbed` is set to `True`.
- The CLI prints a clear `⚠️  Preview generated (stub…)` warning.

When `POST /render` is available, replace `_render_via_storpheus` in the service with a multipart POST call and set `stubbed=False`.

### Agent use case

An AI music generation agent uses `muse render-preview HEAD~10 --json` to obtain a path to the audio preview of a historical snapshot before deciding whether to branch from it or continue the current line.  The `stubbed` field tells the agent whether the file is a true audio render or a MIDI placeholder, so it can adjust its reasoning accordingly.

---

## `muse tempo-scale` — Stretch or Compress the Timing of a Commit

**Purpose:** Apply a deterministic time-scaling transformation to a commit,
stretching or compressing all MIDI note onset/offset times by a factor while
preserving pitch.  Records the result as a new commit, leaving the source
commit intact.  Agents use this to explore half-time grooves, double-time
feels, or to normalise a session to a target BPM before merge.

**Usage:**
```bash
muse tempo-scale [<factor>] [<commit>] [OPTIONS]
```

**Flags:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `<factor>` | float | — | Scaling factor: `0.5` = half-time, `2.0` = double-time |
| `<commit>` | string | HEAD | Source commit SHA to scale |
| `--bpm N` | float | — | Scale to reach exactly N BPM (`factor = N / source_bpm`). Mutually exclusive with `<factor>` |
| `--track TEXT` | string | all | Scale only a specific MIDI track |
| `--preserve-expressions` | flag | off | Scale CC/expression event timing proportionally |
| `--message TEXT` | string | auto | Commit message for the new scaled commit |
| `--json` | flag | off | Emit structured JSON for agent consumption |

> **Note on argument order:** Because `muse tempo-scale` is a Typer group
> command, place all `--options` before the positional `<factor>` argument to
> ensure correct parsing (e.g. `muse tempo-scale --json 2.0`, not
> `muse tempo-scale 2.0 --json`).

**Output example (text):**
```
Tempo scaled: abc12345 -> d9f3a1b2
  Factor:  0.5000  (/2.0000)
  Tempo:   120.0 BPM -> 60.0 BPM
  Track:   all
  Message: tempo-scale 0.5000x (stub)
  (stub -- full MIDI note manipulation pending)
```

**Output example (JSON, `--json`):**
```json
{
  "source_commit": "abc12345",
  "new_commit": "d9f3a1b2",
  "factor": 0.5,
  "source_bpm": 120.0,
  "new_bpm": 60.0,
  "track": "all",
  "preserve_expressions": false,
  "message": "tempo-scale 0.5000x (stub)"
}
```

**Result type:** `TempoScaleResult` (TypedDict) — fields: `source_commit`,
`new_commit`, `factor`, `source_bpm`, `new_bpm`, `track`,
`preserve_expressions`, `message`.

**Factor computation from BPM:**  `factor = target_bpm / source_bpm`.
Example: to go from 120 BPM to 128 BPM, `factor = 128 / 120 ≈ 1.0667`.
Both operations are exposed as pure functions (`compute_factor_from_bpm`,
`apply_factor`) that agents may call directly without spawning the CLI.

**Determinism:** Same `source_commit` + `factor` + `track` + `preserve_expressions`
always produces the same `new_commit` SHA.  This makes tempo-scale operations
safe to cache and replay in agentic pipelines.

**Agent use case:** An AI generating a groove variation queries `muse tempo-scale
--bpm 128 --json` to normalise a 120 BPM sketch to the session BPM before
committing.  A post-generation agent can scan `muse timeline` to verify the
tempo evolution, then use `muse tempo-scale 0.5` to create a half-time B-section
for contrast.

**Implementation:** `maestro/muse_cli/commands/tempo_scale.py` —
`TempoScaleResult` (TypedDict), `compute_factor_from_bpm()`, `apply_factor()`,
`_tempo_scale_async()`, `_format_result()`.  Exit codes: 0 success, 1 bad
arguments (`USER_ERROR`), 2 outside repo (`REPO_NOT_FOUND`), 3 internal error
(`INTERNAL_ERROR`).

> **Stub note:** The current implementation computes the correct schema and
> factor but uses a placeholder 120 BPM as the source tempo and generates a
> deterministic stub commit SHA.  Full MIDI note-event manipulation will be
> wired in when the Storpheus note-event query route is available.

---

## Command Registration Summary

| Command | File | Status | Issue |
|---------|------|--------|-------|
| `muse ask` | `commands/ask.py` | ✅ stub (PR #132) | #126 |
| `muse context` | `commands/context.py` | ✅ implemented (PR #138) | #113 |
| `muse describe` | `commands/describe.py` | ✅ stub (PR #134) | #125 |
| `muse divergence` | `commands/divergence.py` | ✅ implemented (PR #140) | #119 |
| `muse diff` | `commands/diff.py` | ✅ stub (this PR) | #104 |
| `muse dynamics` | `commands/dynamics.py` | ✅ stub (PR #130) | #120 |
| `muse export` | `commands/export.py` | ✅ implemented (PR #137) | #112 |
| `muse grep` | `commands/grep_cmd.py` | ✅ stub (PR #128) | #124 |
| `muse groove-check` | `commands/groove_check.py` | ✅ stub (PR #143) | #95 |
| `muse import` | `commands/import_cmd.py` | ✅ implemented (PR #142) | #118 |
| `muse inspect` | `commands/inspect.py` | ✅ implemented (PR #TBD) | #98 |
| `muse meter` | `commands/meter.py` | ✅ implemented (PR #141) | #117 |
| `muse recall` | `commands/recall.py` | ✅ stub (PR #135) | #122 |
| `muse render-preview` | `commands/render_preview.py` | ✅ implemented (issue #96) | #96 |
| `muse session` | `commands/session.py` | ✅ implemented (PR #129) | #127 |
| `muse swing` | `commands/swing.py` | ✅ stub (PR #131) | #121 |
| `muse tag` | `commands/tag.py` | ✅ implemented (PR #133) | #123 |
| `muse timeline` | `commands/timeline.py` | ✅ implemented (PR #TBD) | #97 |
| `muse tempo-scale` | `commands/tempo_scale.py` | ✅ stub (PR open) | #111 |
| `muse validate` | `commands/validate.py` | ✅ implemented (PR #TBD) | #99 |

All stub commands have stable CLI contracts. Full musical analysis (MIDI content
parsing, vector embeddings, LLM synthesis) is tracked as follow-up issues.

## `muse groove-check` — Rhythmic Drift Analysis

**Purpose:** Detect which commit in a range introduced rhythmic inconsistency
by measuring how much the average note-onset deviation from the quantization grid
changed between adjacent commits.  The music-native equivalent of a style/lint gate.

**Implementation:** `maestro/muse_cli/commands/groove_check.py` (CLI),
`maestro/services/muse_groove_check.py` (pure service layer).
**Status:** ✅ stub (issue #95)

### Usage

```bash
muse groove-check [RANGE] [OPTIONS]
```

### Flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `RANGE` | positional | last 10 commits | Commit range to analyze (e.g. `HEAD~5..HEAD`) |
| `--track TEXT` | string | all | Scope analysis to a specific instrument track (e.g. `drums`) |
| `--section TEXT` | string | all | Scope analysis to a specific musical section (e.g. `verse`) |
| `--threshold FLOAT` | float | 0.1 | Drift threshold in beats; commits exceeding it are flagged WARN; >2× = FAIL |
| `--json` | flag | off | Emit structured JSON for agent consumption |

### Output example

```
Groove-check — range HEAD~6..HEAD  threshold 0.1 beats

Commit    Groove Score  Drift Δ  Status
--------  ------------  -------  ------
a1b2c3d4        0.0400   0.0000  OK
e5f6a7b8        0.0500   0.0100  OK
c9d0e1f2        0.0600   0.0100  OK
a3b4c5d6        0.0900   0.0300  OK
e7f8a9b0        0.1500   0.0600  WARN
c1d2e3f4        0.1300   0.0200  OK

Flagged: 1 / 6 commits  (worst: e7f8a9b0)
```

### Result types

`GrooveStatus` (Enum: OK/WARN/FAIL), `CommitGrooveMetrics` (frozen dataclass),
`GrooveCheckResult` (frozen dataclass).
See `docs/reference/type_contracts.md § GrooveCheckResult`.

### Status classification

| Status | Condition |
|--------|-----------|
| OK | `drift_delta ≤ threshold` |
| WARN | `threshold < drift_delta ≤ 2 × threshold` |
| FAIL | `drift_delta > 2 × threshold` |

### Agent use case

An AI agent runs `muse groove-check HEAD~20..HEAD --json` after a session to
identify which commit degraded rhythmic tightness.  The `worst_commit` field
pinpoints the exact SHA to inspect.  Feeding that into `muse describe` gives
a natural-language explanation of what changed.  If `--threshold 0.05` returns
multiple FAIL commits, the session's quantization workflow needs review before
new layers are added.

### Implementation stub note

`groove_score` and `drift_delta` are computed from deterministic placeholder data.
Full implementation will walk the `MuseCliCommit` chain, load MIDI snapshots via
`MidiParser`, compute per-note onset deviation from the nearest quantization grid
position (resolved from the commit's time-signature + tempo metadata), and
aggregate by track / section.  Storpheus will expose a `/groove` route once
the rhythmic-analysis pipeline is productionized.

---

## `muse contour` — Melodic Contour and Phrase Shape Analysis

**Purpose:** Determines whether a melody rises, falls, arches, or waves — the
fundamental expressive character that distinguishes two otherwise similar
melodies.  An AI generation agent uses `muse contour --json HEAD` to
understand the melodic shape of the current arrangement before layering a
countermelody, ensuring complementary (not identical) contour.

**Usage:**
```bash
muse contour [<commit>] [OPTIONS]
```

**Flags:**
| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `[<commit>]` | string | HEAD | Target commit SHA to analyse |
| `--track TEXT` | string | all tracks | Restrict to a named melodic track (e.g. `keys`, `lead`) |
| `--section TEXT` | string | full piece | Scope analysis to a named section (e.g. `verse`, `chorus`) |
| `--compare COMMIT` | string | — | Compare contour between HEAD (or `[<commit>]`) and this ref |
| `--history` | flag | off | Show contour evolution across all commits |
| `--shape` | flag | off | Print the overall shape label only (one line) |
| `--json` | flag | off | Emit structured JSON for agent consumption |

**Shape vocabulary:**
| Label | Description |
|-------|-------------|
| `ascending` | Net upward movement across the full phrase |
| `descending` | Net downward movement across the full phrase |
| `arch` | Rises then falls (single peak) |
| `inverted-arch` | Falls then rises (valley shape) |
| `wave` | Multiple peaks; alternating rise and fall |
| `static` | Narrow pitch range (< 2 semitones spread) |

**Output example (text):**
```
Shape: Arch | Range: 2 octaves | Phrases: 4 avg 8 bars
Commit: a1b2c3d4  Branch: main
Track: keys  Section: all
Angularity: 2.5 st avg interval
(stub — full MIDI analysis pending)
```

**Output example (`--shape`):**
```
Shape: arch
```

**Output example (`--compare`, text):**
```
A (a1b2c3d4)  Shape: arch | Angularity: 2.5 st
B (HEAD~10)   Shape: arch | Angularity: 2.5 st
Delta  angularity +0.0 st | tessitura +0 st
```

**Output example (`--json`):**
```json
{
  "shape": "arch",
  "tessitura": 24,
  "avg_interval": 2.5,
  "phrase_count": 4,
  "avg_phrase_bars": 8.0,
  "commit": "a1b2c3d4",
  "branch": "main",
  "track": "keys",
  "section": "all",
  "source": "stub"
}
```

**Result types:**
- `ContourResult` — fields: `shape` (str), `tessitura` (int, semitones),
  `avg_interval` (float, semitones), `phrase_count` (int), `avg_phrase_bars`
  (float), `commit` (str), `branch` (str), `track` (str), `section` (str),
  `source` (str).
- `ContourCompareResult` — fields: `commit_a` (ContourResult), `commit_b`
  (ContourResult), `shape_changed` (bool), `angularity_delta` (float),
  `tessitura_delta` (int).

See `docs/reference/type_contracts.md § ContourResult`.

**Agent use case:** Before generating a countermelody, an agent calls
`muse contour --json HEAD --track keys` to determine whether the existing
melody is arch-shaped with a wide tessitura (high angularity).  It then
generates a countermelody that is descending and narrow — complementary, not
imitative.  The `--compare` flag lets the agent detect whether recent edits
made a melody more angular (fragmented) or smoother (stepwise), informing
whether the next variation should introduce or reduce leaps.

**Implementation stub note:** `source: "stub"` in the JSON output indicates
that full MIDI pitch-trajectory analysis is pending a Storpheus pitch-detection
route.  The CLI contract (flags, output shape, result types) is stable — only
the computed values will change when the full implementation is wired in.

**Implementation:** `maestro/muse_cli/commands/contour.py` —
`ContourResult` (TypedDict), `ContourCompareResult` (TypedDict),
`_contour_detect_async()`, `_contour_compare_async()`,
`_contour_history_async()`, `_format_detect()`, `_format_compare()`,
`_format_history()`.  Exit codes: 0 success, 2 outside repo
(`REPO_NOT_FOUND`), 3 internal error (`INTERNAL_ERROR`).

---

### `muse show`

**Purpose:** Inspect any historical commit — its metadata, snapshot manifest,
path-level diff vs parent, MIDI file list, and optionally an audio preview.
The musician's equivalent of `git show`: lets an AI agent or producer examine
exactly what a past creative decision looked like, at any level of detail.

**Usage:**
```bash
muse show [COMMIT] [OPTIONS]
```

**Arguments:**

| Argument | Description |
|----------|-------------|
| `COMMIT` | Commit ID (full or 4–64 char hex prefix), branch name, or `HEAD` (default). |

**Flags:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--json` | flag | off | Output complete commit metadata + snapshot manifest as JSON for agent consumption. |
| `--diff` | flag | off | Show path-level diff vs parent commit with A/M/D status markers. |
| `--midi` | flag | off | List MIDI files (`.mid`, `.midi`, `.smf`) contained in the commit snapshot. |
| `--audio-preview` | flag | off | Open cached audio preview WAV for this snapshot (macOS). Run `muse export <commit> --wav` first. |

Multiple flags can be combined: `muse show abc1234 --diff --midi`.

**Output example (default):**
```
commit a1b2c3d4e5f6...
Branch:  main
Author:  producer@stori.app
Date:    2026-02-27 17:30:00
Parent:  f9e8d7c6

    Add bridge section with Rhodes keys

Snapshot: 3 files
  bass.mid
  beat.mid
  keys.mid
```

**Output example (`--diff`):**
```
diff f9e8d7c6..a1b2c3d4

A  bass.mid
M  beat.mid
D  strings.mid

2 path(s) changed
```

**Output example (`--midi`):**
```
MIDI files in snapshot a1b2c3d4 (3):
  bass.mid  (obj_hash)
  beat.mid  (obj_hash)
  keys.mid  (obj_hash)
```

**Output example (`--json`):**
```json
{
  "commit_id": "a1b2c3d4e5f6...",
  "branch": "main",
  "parent_commit_id": "f9e8d7c6...",
  "parent2_commit_id": null,
  "message": "Add bridge section with Rhodes keys",
  "author": "producer@stori.app",
  "committed_at": "2026-02-27 17:30:00",
  "snapshot_id": "snap_sha256...",
  "snapshot_manifest": {
    "bass.mid": "obj_sha256_a",
    "beat.mid": "obj_sha256_b",
    "keys.mid": "obj_sha256_c"
  }
}
```

**Result types:**
- `ShowCommitResult` (TypedDict) — full commit metadata + snapshot manifest returned by `_show_async()`.
- `ShowDiffResult` (TypedDict) — path-level diff (added/modified/removed lists + total_changed) returned by `_diff_vs_parent_async()`.

**Commit resolution order:**
1. `HEAD` (case-insensitive) → follows the `HEAD` ref file to the current branch tip.
2. 4–64 character hex string → exact commit ID match first, then prefix scan.
3. Anything else → treated as a branch name; reads `.muse/refs/heads/<name>`.

**Agent use case:** An AI music generation agent calls `muse show HEAD` to inspect the
latest committed snapshot before generating the next variation — confirming which
instruments are present, what files changed in the last commit, and whether there are
MIDI files it can use as seeds for generation. Use `--json` for structured consumption
in agent pipelines. Use `--diff` to understand what changed in the last session.
Use `--midi` to enumerate MIDI seeds for the Storpheus generation pipeline.

**`--audio-preview` note:** The full render-preview pipeline (Storpheus → WAV) is
invoked via `muse export <commit> --wav`. The `--audio-preview` flag then plays the
cached WAV via `afplay` (macOS). If no cached file exists, a clear help message is
printed instead.

---

## `muse amend` — Amend the Most Recent Commit

**Purpose:** Fold working-tree changes into the most recent commit, replacing
it with a new commit that has the same parent.  Equivalent to
`git commit --amend`.  The original HEAD commit becomes an orphan (unreachable
from any branch ref) and remains in the database for forensic traceability.

**Usage:**
```bash
muse amend [OPTIONS]
```

**Flags:**
| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-m / --message TEXT` | string | — | Replace the commit message |
| `--no-edit` | flag | off | Keep the original commit message (default when `-m` is omitted; takes precedence over `-m` when both are provided) |
| `--reset-author` | flag | off | Reset the author field to the current user (stub: sets to empty string until a user-identity system is implemented) |

**Output example:**
```
✅ [main a1b2c3d4] updated groove pattern (amended)
```

**Behaviour:**
1. Re-snapshots `muse-work/` using the same content-addressed pipeline as
   `muse commit` (sha256 per file, deterministic snapshot_id).
2. Computes a new `commit_id` using the *original commit's parent* (not the
   original itself), the new snapshot, the effective message, and the current
   timestamp.
3. Writes the new commit row to Postgres and updates
   `.muse/refs/heads/<branch>` to the new commit ID.
4. **Blocked** when a merge is in progress (`.muse/MERGE_STATE.json` exists).
5. **Blocked** when there are no commits yet on the current branch.
6. **Blocked** when `muse-work/` does not exist or is empty.

**Result types:**
- Returns the new `commit_id` (64-char sha256 hex string) from `_amend_async`.
- Exit codes: 0 success, 1 user error (`USER_ERROR`), 2 outside repo
  (`REPO_NOT_FOUND`), 3 internal error (`INTERNAL_ERROR`).

**Agent use case:** A producer adjusts a MIDI note quantization setting, then
runs `muse amend --no-edit` to fold the change silently into the last commit
without cluttering history with a second "tweak quantization" entry.  An
automated agent can call `muse amend -m "fix: tighten quantization on drums"`
to improve the commit message after inspection.

**Implementation:** `maestro/muse_cli/commands/amend.py` —
`_amend_async(message, no_edit, reset_author, root, session)`.
Tests: `tests/muse_cli/test_amend.py`.

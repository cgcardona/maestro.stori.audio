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
    ├── init.py           — muse init  ✅ fully implemented (--bare, --template, --default-branch added in issue #85)
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
    ├── remote.py         — muse remote (add, -v)
    ├── fetch.py          — muse fetch
    ├── push.py           — muse push
    ├── pull.py           — muse pull
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

**Usage:**
```bash
muse merge <branch> [OPTIONS]
```

**Flags:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--no-ff` | flag | off | Force a merge commit even when fast-forward is possible. Preserves branch topology in the history graph. |
| `--squash` | flag | off | Collapse all commits from `<branch>` into one new commit on the current branch. The result has a single parent and no `parent2_commit_id` — not a merge commit in the DAG. |
| `--strategy TEXT` | string | none | Resolution shortcut: `ours` keeps all files from the current branch; `theirs` takes all files from the target branch. Both skip conflict detection. |
| `--continue` | flag | off | Finalize a paused merge after resolving all conflicts with `muse resolve`. |

### Algorithm

1. **Guard** — If `.muse/MERGE_STATE.json` exists, a merge is already in progress. Exit 1 with: *"Merge in progress. Resolve conflicts and run `muse merge --continue`."*
2. **Resolve commits** — Read HEAD commit ID for the current branch and the target branch from their `.muse/refs/heads/<branch>` ref files.
3. **Find merge base** — BFS over the commit graph to find the LCA (Lowest Common Ancestor) of the two HEAD commits. Both `parent_commit_id` and `parent2_commit_id` are traversed (supporting existing merge commits).
4. **Fast-forward** — If `base == ours` *and* `--no-ff` is not set *and* `--squash` is not set, the target is strictly ahead of current HEAD. Move the current branch pointer to `theirs` without creating a new commit.
5. **Already up-to-date** — If `base == theirs`, current branch is already ahead. Exit 0.
6. **Strategy shortcut** — If `--strategy ours` or `--strategy theirs` is set, apply the resolution immediately before conflict detection and proceed to create a merge commit. No conflict state is written.
7. **3-way merge** — When branches have diverged and no strategy is set:
   - Compute `diff(base → ours)` and `diff(base → theirs)` at file-path granularity.
   - Detect conflicts: paths changed on *both* sides since the base.
   - If **no conflicts**: auto-merge (take the changed side for each path), create a merge commit with two parent IDs, advance the branch pointer.
   - If **conflicts**: write `.muse/MERGE_STATE.json` and exit 1 with a conflict summary.
8. **Squash** — If `--squash` is set, create a single commit with a combined tree but only `parent_commit_id` = current HEAD. `parent2_commit_id` is `None`.

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

A successful 3-way merge (or `--no-ff` or `--strategy`) creates a commit with:
- `parent_commit_id` = `ours_commit_id` (current branch HEAD at merge time)
- `parent2_commit_id` = `theirs_commit_id` (target branch HEAD)
- `snapshot_id` = merged manifest (non-conflicting changes from both sides)
- `message` = `"Merge branch '<branch>' into <current_branch>"` (strategy appended if set)

### Squash Commit

`--squash` creates a commit with:
- `parent_commit_id` = `ours_commit_id` (current branch HEAD)
- `parent2_commit_id` = `None` — not a merge commit in the graph
- `snapshot_id` = same merged manifest as a regular merge would produce
- `message` = `"Squash merge branch '<branch>' into <current_branch>"`

Use squash when you want to land a feature branch as one clean commit without
polluting `muse log` with intermediate work-in-progress commits.

### Path-Level Granularity (MVP)

This merge implementation operates at **file-path level**. Two commits that modify the same file path (even if the changes are disjoint within the file) are treated as a conflict. Note-level merging (music-aware diffs inside MIDI files) is a future enhancement reserved for the existing `maestro/services/muse_merge.py` engine.

### Agent Use Case

- **`--no-ff`**: Use when building a structured session history is important (e.g., preserving that a feature branch existed). The branch topology is visible in `muse log --graph`.
- **`--squash`**: Use after iterative experimentation on a feature branch to produce one atomic commit for review. Equivalent to "clean up before sharing."
- **`--strategy ours`**: Use to quickly resolve a conflict situation where the current branch's version is definitively correct (e.g., a hotfix already applied to main).
- **`--strategy theirs`**: Use to accept all incoming changes wholesale (e.g., adopting a new arrangement from a collaborator).

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

`muse status` operates in several modes depending on repository state and active flags.

**Usage:**
```bash
muse status [OPTIONS]
```

**Flags:**

| Flag | Short | Description |
|------|-------|-------------|
| `--short` | `-s` | Condensed one-line-per-file output (`M`=modified, `A`=added, `D`=deleted, `?`=untracked) |
| `--branch` | `-b` | Emit only the branch and tracking info line |
| `--porcelain` | — | Machine-readable `XY path` format, stable for scripting (like `git status --porcelain`) |
| `--sections` | — | Group output by first path component under `muse-work/` (musical sections) |
| `--tracks` | — | Group output by first path component under `muse-work/` (instrument tracks) |

Flags are combinable where it makes sense: `--short --sections` emits short-format codes grouped under section headers; `--porcelain --tracks` emits porcelain codes grouped under track headers.

### Mode 1 — Clean working tree

No changes since the last commit:

```
On branch main
nothing to commit, working tree clean
```

With `--porcelain` (clean): emits only the branch header `## main`.

### Mode 2 — Uncommitted changes

Files have been modified, added, or deleted relative to the last snapshot:

**Default (verbose):**
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

**`--short`:**
```
On branch main
M beat.mid
A lead.mp3
D scratch.mid
```

**`--porcelain`:**
```
## main
 M beat.mid
 A lead.mp3
 D scratch.mid
```

The two-character code column follows the git porcelain convention: first char = index, second = working tree. Since Muse tracks working-tree changes only, the first char is always a space.

**`--sections` (group by musical section directory):**
```
On branch main

## chorus
	modified:   chorus/bass.mid

## verse
	modified:   verse/bass.mid
	new file:   verse/drums.mid
```

**`--tracks` (group by instrument track directory):**
```
On branch main

## bass
	modified:   bass/verse.mid

## drums
	new file:   drums/chorus.mid
```

Files not under a subdirectory appear under `## (root)` when grouping is active.

**Combined `--short --sections`:**
```
On branch main
## chorus
M chorus/bass.mid

## verse
M verse/bass.mid
```

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

### `--branch` only

Emits only the branch line regardless of working-tree state:

```
On branch main
```

This is useful when a script needs the branch name without triggering a full DB round-trip for the diff.

### Agent use case

An AI music agent uses `muse status` to:

- **Detect drift:** `muse status --porcelain` gives a stable, parseable list of all changed files before deciding whether to commit.
- **Section-aware generation:** `muse status --sections` reveals which musical sections have uncommitted changes, letting the agent focus generation on modified sections only.
- **Track inspection:** `muse status --tracks` shows which instrument tracks differ from HEAD, useful when coordinating multi-track edits across agent turns.
- **Pre-commit guard:** `muse status --short` gives a compact human-readable summary to include in agent reasoning traces before committing.

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
| `message` | `Text` | User-supplied commit message (may include Co-authored-by trailers) |
| `author` | `String(255)` | Reserved (empty for MVP) |
| `committed_at` | `DateTime(tz=True)` | Timestamp used in hash derivation |
| `created_at` | `DateTime(tz=True)` | Wall-clock DB insert time |
| `metadata` | `JSON` nullable | Extensible music-domain annotations (see below) |

**`metadata` JSON blob — current keys:**

| Key | Type | Set by |
|-----|------|--------|
| `section` | `string` | `muse commit --section` |
| `track` | `string` | `muse commit --track` |
| `emotion` | `string` | `muse commit --emotion` |
| `tempo_bpm` | `float` | `muse tempo --set` |

All keys are optional and co-exist in the same blob.  Absent keys are simply not present (not `null`).  Future music-domain annotations extend this blob without schema migrations.

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
  repo.json          Repo identity: repo_id (UUID), schema_version, created_at[, bare]
  HEAD               Current branch pointer, e.g. "refs/heads/main"
  config.toml        [core] (bare repos only), [user], [auth], [remotes] configuration
  objects/           Local content-addressed object store (written by muse commit)
    <object_id>      One file per unique object (sha256 of file bytes)
  refs/
    heads/
      main           Commit ID of branch HEAD (empty = no commits yet)
      <branch>       One file per branch
muse-work/           Working-tree root (absent for --bare repos)
```

### `muse init` flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--bare` | flag | off | Initialise as a bare repository — no `muse-work/` checkout. Writes `bare = true` into `repo.json` and `[core] bare = true` into `config.toml`. Used for Muse Hub remote/server-side repos. |
| `--template PATH` | path | — | Copy the contents of *PATH* into `muse-work/` after initialisation. Lets studios pre-populate a standard folder structure (e.g. `drums/`, `bass/`, `keys/`, `vocals/`) for every new project. Ignored when `--bare` is set. |
| `--default-branch BRANCH` | text | `main` | Name of the initial branch. Sets `HEAD → refs/heads/<BRANCH>` and creates the matching ref file. |
| `--force` | flag | off | Re-initialise even if `.muse/` already exists. Preserves the existing `repo_id` so remote-tracking metadata stays coherent. Does not overwrite `config.toml`. |

**Bare repository layout** (`--bare`):

```
.muse/
  repo.json          … bare = true …
  HEAD               refs/heads/<branch>
  refs/heads/<branch>
  config.toml        [core] bare = true  + [user] [auth] [remotes] stubs
```

Bare repos are used as Muse Hub remotes — objects and refs only, no live working copy.

**Usage examples:**

```bash
muse init                                     # standard repo, branch = main
muse init --default-branch develop            # standard repo, branch = develop
muse init --bare                              # bare repo (Hub remote)
muse init --bare --default-branch trunk       # bare repo, branch = trunk
muse init --template /path/to/studio-tmpl     # copy template into muse-work/
muse init --template /studio --default-branch release  # template + custom branch
muse init --force                             # reinitialise, preserve repo_id
```

### File semantics

| File | Source of truth for | Notes |
|------|-------------------|-------|
| `repo.json` | Repo identity | `repo_id` persists across `--force` reinitialise; `bare = true` written for bare repos |
| `HEAD` | Current branch name | Always `refs/heads/<branch>`; branch name set by `--default-branch` |
| `refs/heads/<branch>` | Branch → commit pointer | Empty string = branch has no commits yet |
| `config.toml` | User identity, auth token, remotes | Not overwritten on `--force`; bare repos include `[core] bare = true` |
| `muse-work/` | Working-tree root | Created by non-bare init; populated from `--template` if provided |

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

### `muse commit` — Full Flag Reference

**Usage:**
```bash
muse commit -m <message> [OPTIONS]
muse commit --from-batch muse-batch.json [OPTIONS]
```

**Core flags:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-m / --message TEXT` | string | — | Commit message. Required unless `--from-batch` is used |
| `--from-batch PATH` | path | — | Use `commit_message_suggestion` from `muse-batch.json`; snapshot is restricted to listed files |
| `--amend` | flag | off | Fold working-tree changes into the most recent commit (equivalent to `muse amend`) |
| `--no-verify` | flag | off | Bypass pre-commit hooks (no-op until hook system is implemented) |
| `--allow-empty` | flag | off | Allow committing even when the working tree has not changed since HEAD |

**Music-domain flags (Muse-native metadata):**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--section TEXT` | string | — | Tag commit as belonging to a musical section (e.g. `verse`, `chorus`, `bridge`) |
| `--track TEXT` | string | — | Tag commit as affecting a specific instrument track (e.g. `drums`, `bass`, `keys`) |
| `--emotion TEXT` | string | — | Attach an emotion vector label (e.g. `joyful`, `melancholic`, `tense`) |
| `--co-author TEXT` | string | — | Append `Co-authored-by: Name <email>` trailer to the commit message |

Music-domain flags are stored in the `commit_metadata` JSON column on `muse_cli_commits`.  They are surfaced at the top level in `muse show <commit> --json` output and form the foundation for future queries like `muse log --emotion melancholic` or `muse diff --section chorus`.

**Examples:**

```bash
# Standard commit with message
muse commit -m "feat: add Rhodes piano to chorus"

# Tag with music-domain metadata
muse commit -m "groove take 3" --section verse --track drums --emotion joyful

# Collaborative session — attribute a co-author
muse commit -m "keys arrangement" --co-author "Alice <alice@stori.app>"

# Amend the last commit with new emotion tag
muse commit --amend --emotion melancholic

# Milestone commit with no file changes
muse commit --allow-empty -m "session handoff" --section bridge

# Fast path from stress test
muse commit --from-batch muse-batch.json --emotion tense
```

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
`--from-batch`'s suggestion wins.  All music-domain flags (`--section`, `--track`,
`--emotion`, `--co-author`) can be combined with `--from-batch`.

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

### `muse hash-object`

**Purpose:** Compute the SHA-256 content-address of a file (or stdin) and
optionally write it into the Muse object store.  The hash produced is
identical to what `muse commit` would assign to the same file, ensuring
cross-command content-addressability.  Use this for scripting, pre-upload
deduplication checks, and debugging the object store.

**Usage:**
```bash
muse hash-object <file> [OPTIONS]
muse hash-object --stdin [OPTIONS]
```

**Flags:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `<file>` | positional | — | Path to the file to hash.  Omit when using `--stdin`. |
| `-w / --write` | flag | off | Write the object to `.muse/objects/` and the `muse_cli_objects` table in addition to printing the hash. |
| `--stdin` | flag | off | Read content from stdin instead of a file. |

**Output example:**

```
a3f2e1b0d4c5...  (64-character SHA-256 hex digest)
```

**Result type:** `HashObjectResult` — fields: `object_id` (str, 64-char hex), `stored` (bool), `already_existed` (bool).

**Agent use case:** An AI agent can call `muse hash-object <file>` to derive the
object ID before committing, enabling optimistic checks ("is this drum loop
already in the store?") without running a full `muse commit`.  Piping output
to `muse cat-object` verifies whether the stored content matches expectations.

**Implementation:** `maestro/muse_cli/commands/hash_object.py` — registered as
`muse hash-object`.  `HashObjectResult` (class), `hash_bytes()` (pure helper),
`_hash_object_async()` (fully injectable for tests).

---

## Muse CLI — Remote Sync Command Reference

These commands connect the local Muse repo to the remote Muse Hub, enabling
collaboration between musicians (push from one machine, pull on another) and
serving as the CLI-side counterpart to the Hub's sync API.

---

### `muse remote`

**Purpose:** Manage named remote Hub URLs in `.muse/config.toml`.  Every push
and pull needs a remote configured — `muse remote add` is the prerequisite.

**Usage:**
```bash
muse remote add <name> <url>   # register a remote
muse remote -v                  # list all remotes
```

**Flags:**
| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-v` / `--verbose` | flag | off | Print all configured remotes with their URLs |

**Subcommands:**

| Subcommand | Description |
|-----------|-------------|
| `add <name> <url>` | Write `[remotes.<name>] url = "<url>"` to `.muse/config.toml` |

**Output example:**
```
# muse remote add origin https://story.audio/musehub/repos/my-repo-id
✅ Remote 'origin' set to https://story.audio/musehub/repos/my-repo-id

# muse remote -v
origin  https://story.audio/musehub/repos/my-repo-id
staging https://staging.example.com/musehub/repos/my-repo-id
```

**Security:** Token values in `[auth]` are never shown by `muse remote -v`.

**Exit codes:** 0 — success; 1 — bad URL or empty name; 2 — not a repo.

**Agent use case:** An orchestration agent registers the Hub URL once at repo
setup time; subsequent push/pull commands run without further config.

---

### `muse push`

**Purpose:** Upload local commits that the remote Hub does not yet have.
Enables collaborative workflows where one musician pushes and others pull.
Supports force-push, lease-guarded override, tag syncing, and upstream tracking.

**Usage:**
```bash
muse push
muse push --branch feature/groove-v2
muse push --remote staging
muse push --force-with-lease
muse push --force -f
muse push --tags
muse push --set-upstream -u
```

**Flags:**
| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--branch` / `-b` | str | current branch | Branch to push |
| `--remote` | str | `origin` | Named remote to push to |
| `--force` / `-f` | flag | off | Overwrite remote branch even on non-fast-forward. Use with caution — this discards remote history. |
| `--force-with-lease` | flag | off | Overwrite remote only if its current HEAD matches our last-known tracking pointer. Safer than `--force`; the Hub must return HTTP 409 if the remote has advanced. |
| `--tags` | flag | off | Push all VCS-style tag refs from `.muse/refs/tags/` alongside the branch commits. |
| `--set-upstream` / `-u` | flag | off | After a successful push, record the remote as the upstream for the current branch in `.muse/config.toml`. |

**Push algorithm:**
1. Read `repo_id` from `.muse/repo.json` and branch from `.muse/HEAD`.
2. Read local HEAD commit from `.muse/refs/heads/<branch>`.
3. Resolve remote URL from `[remotes.<name>] url` in `.muse/config.toml`.
4. Read last-known remote HEAD from `.muse/remotes/<name>/<branch>` (absent on first push).
5. Compute delta: commits from local HEAD down to (but not including) remote HEAD.
6. If `--tags`, enumerate `.muse/refs/tags/` and include as `PushTagPayload` list.
7. POST `{ branch, head_commit_id, commits[], objects[], [force], [force_with_lease], [expected_remote_head], [tags] }` to `<remote>/push`.
8. On HTTP 200, update `.muse/remotes/<name>/<branch>` to the new HEAD; if `--set-upstream`, write `branch = <branch>` under `[remotes.<name>]` in `.muse/config.toml`.
9. On HTTP 409 with `--force-with-lease`, exit 1 with instructive message.

**Force-with-lease contract:** `expected_remote_head` is the commit ID in our local
tracking pointer before the push. The Hub must compare it against its current HEAD and
reject (HTTP 409) if they differ — this prevents clobbering commits pushed by others
since our last fetch.

**Output example:**
```
⬆️  Pushing 3 commit(s) to origin/main [--force-with-lease] …
✅ Branch 'main' set to track 'origin/main'
✅ Pushed 3 commit(s) → origin/main [aabbccdd]

# When force-with-lease rejected:
❌ Push rejected: remote origin/main has advanced since last fetch.
   Run `muse pull` then retry, or use `--force` to override.
```

**Exit codes:** 0 — success; 1 — no remote, no commits, or force-with-lease mismatch; 3 — network/server error.

**Result type:** `PushRequest` / `PushResponse` — see `maestro/muse_cli/hub_client.py`.
New TypedDicts: `PushTagPayload` (tag_name, commit_id).

**Agent use case:** After `muse commit`, an agent runs `muse push` to publish
the committed variation to the shared Hub. For CI workflows, `--force-with-lease`
prevents clobbering concurrent pushes from other agents.

---

### `muse pull`

**Purpose:** Download commits from the remote Hub that are missing locally,
then integrate them into the local branch via fast-forward, merge, or rebase.
After pull, the AI agent has the full commit history of remote collaborators
available for `muse context`, `muse diff`, `muse ask`, etc.

**Usage:**
```bash
muse pull
muse pull --rebase
muse pull --ff-only
muse pull --branch feature/groove-v2
muse pull --remote staging
```

**Flags:**
| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--branch` / `-b` | str | current branch | Branch to pull |
| `--remote` | str | `origin` | Named remote to pull from |
| `--rebase` | flag | off | After fetching, rebase local commits onto remote HEAD rather than merge. Fast-forwards when remote is simply ahead; replays local commits (linear rebase) when diverged. |
| `--ff-only` | flag | off | Only integrate if the result would be a fast-forward. Fails with exit 1 and leaves local branch unchanged if branches have diverged. |

**Pull algorithm:**
1. Resolve remote URL from `[remotes.<name>] url` in `.muse/config.toml`.
2. Collect `have_commits` (all local commit IDs) and `have_objects` (all local object IDs).
3. POST `{ branch, have_commits[], have_objects[], [rebase], [ff_only] }` to `<remote>/pull`.
4. Store returned commits and object descriptors in local Postgres.
5. Update `.muse/remotes/<name>/<branch>` tracking pointer.
6. Apply post-fetch integration strategy:
   - **Default:** If diverged, print warning and suggest `muse merge`.
   - **`--ff-only`:** If `local_head` is ancestor of `remote_head`, advance branch ref (fast-forward). Otherwise exit 1.
   - **`--rebase`:** If `local_head` is ancestor of `remote_head`, fast-forward. If diverged, find merge base and replay local commits above base onto `remote_head` using `compute_commit_tree_id` (deterministic IDs).

**Rebase contract:** Linear rebase only — no path-level conflict detection.
For complex divergence with conflicting file changes, use `muse merge`.
The rebased commit IDs are deterministic (via `compute_commit_tree_id`), so
re-running the same rebase is idempotent.

**Divergence detection:** Pull succeeds (exit 0) even when diverged in default
mode. The divergence warning is informational.

**Output example:**
```
⬇️  Pulling origin/main (--rebase) …
✅ Fast-forwarded main → aabbccdd
✅ Pulled 2 new commit(s), 5 new object(s) from origin/main

# Diverged + --rebase:
⟳  Rebasing 2 local commit(s) onto aabbccdd …
✅ Rebase complete — main → eeff1122
✅ Pulled 3 new commit(s), 0 new object(s) from origin/main

# Diverged + --ff-only:
❌ Cannot fast-forward: main has diverged from origin/main.
   Run `muse merge origin/main` or use `muse pull --rebase` to integrate.
```

**Exit codes:** 0 — success; 1 — no remote, or `--ff-only` on diverged branch; 3 — network/server error.

**Result type:** `PullRequest` / `PullResponse` — see `maestro/muse_cli/hub_client.py`.

**Agent use case:** Before generating a new arrangement, an agent runs
`muse pull --rebase` to ensure it works from the latest shared composition
state with a clean linear history. `--ff-only` is useful in strict CI pipelines
where merges are not permitted.

---

### `muse fetch`

**Purpose:** Update remote-tracking refs to reflect the current state of the remote
without modifying the local branch or muse-work/.  Use `muse fetch` when you want
to inspect what collaborators have pushed before deciding whether to merge.  This
is the non-destructive alternative to `muse pull` (fetch + merge).

**Usage:**
```bash
muse fetch
muse fetch --all
muse fetch --prune
muse fetch --remote staging --branch main --branch feature/bass-v2
```

**Flags:**
| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--remote` | str | `origin` | Named remote to fetch from |
| `--all` | flag | off | Fetch from every configured remote |
| `--prune` / `-p` | flag | off | Remove local remote-tracking refs for branches deleted on the remote |
| `--branch` / `-b` | str (repeatable) | all branches | Specific branch(es) to fetch |

**Fetch algorithm:**
1. Resolve remote URL(s) from `[remotes.<name>] url` in `.muse/config.toml`.
2. POST `{ branches: [] }` (empty = all) to `<remote>/fetch`.
3. For each branch in the Hub response, update `.muse/remotes/<remote>/<branch>` with the remote HEAD commit ID.
4. If `--prune`, remove any `.muse/remotes/<remote>/<branch>` files whose branch was NOT in the Hub response.
5. Local branches (`refs/heads/`) and `muse-work/` are NEVER modified.

**Fetch vs Pull:**
| Operation | Modifies local branch | Modifies muse-work/ | Merges remote commits |
|-----------|----------------------|---------------------|----------------------|
| `muse fetch` | No | No | No |
| `muse pull` | Yes (via merge) | Yes | Yes |

**Output example:**
```
From origin: + abc1234 feature/guitar -> origin/feature/guitar (new branch)
From origin: + def5678 main -> origin/main
✅ origin is already up to date.

# With --all:
From origin: + abc1234 main -> origin/main
From staging: + xyz9999 main -> staging/main
✅ Fetched 2 branch update(s) across all remotes.

# With --prune:
✂️  Pruned origin/deleted-branch (no longer exists on remote)
```

**Exit codes:** 0 — success; 1 — no remote configured or `--all` with no remotes; 3 — network/server error.

**Result type:** `FetchRequest` / `FetchResponse` / `FetchBranchInfo` — see `maestro/muse_cli/hub_client.py`.

**Agent use case:** An agent runs `muse fetch` before deciding whether to compose a new
variation, to check if remote collaborators have pushed conflicting changes.  Since fetch
does not modify the working tree, it is safe to run mid-composition without interrupting
the current generation pipeline.  Follow with `muse log origin/main` to inspect what
arrived, then `muse merge origin/main` if the agent decides to incorporate remote changes.

---

## Muse CLI — Music Analysis Command Reference

These commands expose musical dimensions across the commit graph — the layer that
makes Muse fundamentally different from Git. Each command is consumed by AI agents
to make musically coherent generation decisions. Every flag is part of a stable
CLI contract; stub implementations are clearly marked.

**Agent pattern:** Run with `--json` to get machine-readable output. Pipe into
`muse context` for a unified musical state document.

---

### `muse cat-object`

**Purpose:** Read and display a raw Muse object by its SHA-256 hash. The
plumbing equivalent of `git cat-file` — lets an AI agent inspect any stored
blob, snapshot manifest, or commit record without running the full `muse log`
pipeline.

**Usage:**
```bash
muse cat-object [OPTIONS] <object-id>
```

**Flags:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `<object-id>` | positional | required | Full 64-char SHA-256 hash to look up |
| `-t / --type` | flag | off | Print only the object type (`object`, `snapshot`, or `commit`) |
| `-p / --pretty` | flag | off | Pretty-print the object content as indented JSON |

`-t` and `-p` are mutually exclusive.

**Output example (default):**
```
type:       commit
commit_id:  a1b2c3d4...
branch:     main
snapshot:   f9e8d7c6...
message:    boom bap demo take 1
parent:     00112233...
committed_at: 2026-02-27T17:30:00+00:00
```

**Output example (`-t`):**
```
commit
```

**Output example (`-p <snapshot_id>`):**
```json
{
  "type": "snapshot",
  "snapshot_id": "f9e8d7c6...",
  "manifest": {
    "beat.mid": "a1b2c3d4...",
    "keys.mid": "11223344..."
  },
  "created_at": "2026-02-27T17:20:00+00:00"
}
```

**Result type:** `CatObjectResult` — fields: `object_type` (str), `row`
(MuseCliObject | MuseCliSnapshot | MuseCliCommit). Call `.to_dict()` for a
JSON-serialisable representation.

**Agent use case:** Use `muse cat-object -t <hash>` to determine the type of
an unknown ID before deciding how to process it. Use `-p` to extract the
snapshot manifest (file → object_id map) or commit metadata for downstream
generation context. Combine with `muse log` short IDs: copy the full commit_id
from `muse log`, then `muse cat-object -p <id>` to inspect its snapshot.

**Error behaviour:** Exits with code 1 (`USER_ERROR`) when the ID is not found
in any object table; prints `❌ Object not found: <id>`.

---

### `muse harmony`

**Purpose:** Analyze the harmonic content (key center, mode, chord progression, harmonic
rhythm, and tension profile) of a commit. The primary tool for understanding what a
composition is doing harmonically — information that is completely invisible to Git.
An AI agent calling `muse harmony --json` knows whether the current arrangement is in
Eb major with a II-V-I progression and moderate tension, and can use this to make
musically coherent generation decisions.

**Usage:**
```bash
muse harmony [<commit>] [OPTIONS]
```

**Flags:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--track TEXT` | string | all tracks | Restrict to a named MIDI track (e.g. `--track keys`) |
| `--section TEXT` | string | — | Restrict to a named musical section/region (planned) |
| `--compare COMMIT` | string | — | Compare harmonic content against another commit |
| `--range FROM..TO` | string | — | Analyze across a commit range (planned) |
| `--progression` | flag | off | Show only the chord progression sequence |
| `--key` | flag | off | Show only the detected key center |
| `--mode` | flag | off | Show only the detected mode |
| `--tension` | flag | off | Show only the harmonic tension profile |
| `--json` | flag | off | Emit structured JSON for agent consumption |

**Output example (text):**
```
Commit abc1234 — Harmonic Analysis
(stub — full MIDI analysis pending)

Key: Eb (confidence: 0.92)
Mode: major
Chord progression: Ebmaj7 | Fm7 | Bb7sus4 | Bb7 | Ebmaj7 | Abmaj7 | Gm7 | Cm7
Harmonic rhythm: 2.1 chords/bar avg
Tension profile: Low → Medium → High → Resolution (textbook tension-release arc)  [0.2 → 0.4 → 0.8 → 0.3]
```

**Output example (`--json`):**
```json
{
  "commit_id": "abc1234",
  "branch": "main",
  "key": "Eb",
  "mode": "major",
  "confidence": 0.92,
  "chord_progression": ["Ebmaj7", "Fm7", "Bb7sus4", "Bb7", "Ebmaj7", "Abmaj7", "Gm7", "Cm7"],
  "harmonic_rhythm_avg": 2.1,
  "tension_profile": [0.2, 0.4, 0.8, 0.3],
  "track": "all",
  "source": "stub"
}
```

**Output example (`--compare <commit> --json`):**
```json
{
  "head": { "commit_id": "abc1234", "key": "Eb", "mode": "major", ... },
  "compare": { "commit_id": "def5678", "key": "Eb", "mode": "major", ... },
  "key_changed": false,
  "mode_changed": false,
  "chord_progression_delta": []
}
```

**Result type:** `HarmonyResult` — fields: `commit_id`, `branch`, `key`, `mode`,
`confidence`, `chord_progression`, `harmonic_rhythm_avg`, `tension_profile`, `track`, `source`.
Compare path returns `HarmonyCompareResult` — fields: `head`, `compare`, `key_changed`,
`mode_changed`, `chord_progression_delta`.

**Agent use case:** Before generating a new instrument layer, an agent calls
`muse harmony --json` to discover the harmonic context. If the arrangement is in
Eb major with a II-V-I progression, the agent ensures its generated voicings stay
diatonic to Eb. If the tension profile shows a build toward the chorus, the agent
adds chromatic tension at the right moment rather than resolving early.
`muse harmony --compare HEAD~5 --json` reveals whether the composition has
modulated, shifted mode, or changed its harmonic rhythm — all decisions an AI
needs to make coherent musical choices across versions.

**Implementation:** `maestro/muse_cli/commands/harmony.py` — `_harmony_analyze_async`
(injectable async core), `HarmonyResult` / `HarmonyCompareResult` (TypedDict result
entities), `_stub_harmony` (placeholder data), `_tension_label` (arc classifier),
`_render_result_human` / `_render_result_json` / `_render_compare_human` /
`_render_compare_json` (renderers). Exit codes: 0 success, 2 outside repo, 3 internal.

> **Stub note:** Chord detection, key inference, and tension computation are placeholder
> values derived from a static Eb major II-V-I template. Full implementation requires
> MIDI note extraction from committed snapshot objects (future: Storpheus chord detection
> route). The CLI contract, result types, and flag set are stable.

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

### `muse transpose`

**Purpose:** Apply MIDI pitch transposition to all files in `muse-work/` and record the result as a new Muse commit. Transposition is the most fundamental musical transformation — this makes it a first-class versioned operation rather than a silent destructive edit. Drum channels (MIDI channel 9) are always excluded because drums are unpitched.

**Usage:**
```bash
muse transpose <interval> [<commit>] [OPTIONS]
```

**Flags:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `<interval>` | positional | required | Signed integer (`+3`, `-5`) or named interval (`up-minor3rd`, `down-perfect5th`) |
| `[<commit>]` | positional | HEAD | Source commit to transpose from |
| `--track TEXT` | string | all tracks | Transpose only the MIDI track whose name contains TEXT (case-insensitive substring) |
| `--section TEXT` | string | — | Transpose only a named section (stub — full implementation pending) |
| `--message TEXT` | string | `"Transpose +N semitones"` | Custom commit message |
| `--dry-run` | flag | off | Show what would change without writing files or creating a commit |
| `--json` | flag | off | Emit machine-readable JSON output |

**Interval syntax:**

| Form | Example | Semitones |
|------|---------|-----------|
| Signed integer | `+3` | +3 |
| Signed integer | `-5` | -5 |
| Named up | `up-minor3rd` | +3 |
| Named down | `down-perfect5th` | -7 |
| Named down | `down-octave` | -12 |

**Named interval identifiers:**
`unison`, `minor2nd`, `major2nd`, `minor3rd`, `major3rd`, `perfect4th`,
`perfect5th`, `minor6th`, `major6th`, `minor7th`, `major7th`, `octave`
(prefix with `up-` or `down-`)

**Output example (text):**
```
✅ [a1b2c3d4] Transpose +3 semitones
   Key: Eb major  →  F# major
   Modified: 2 file(s)
     ✅ tracks/melody.mid
     ✅ tracks/bass.mid
   Skipped:  1 file(s) (non-MIDI or no pitched notes)
```

**Output example (`--json`):**
```json
{
  "source_commit_id": "a1b2c3d4...",
  "semitones": 3,
  "files_modified": ["tracks/melody.mid", "tracks/bass.mid"],
  "files_skipped": ["notes.json"],
  "new_commit_id": "b2c3d4e5...",
  "original_key": "Eb major",
  "new_key": "F# major",
  "dry_run": false
}
```

**Result type:** `TransposeResult` — fields: `source_commit_id`, `semitones`, `files_modified`, `files_skipped`, `new_commit_id` (None in dry-run), `original_key`, `new_key`, `dry_run`.

**Key metadata update:** If the source commit has a `key` field in its `metadata` JSON blob (e.g. `"Eb major"`), the new commit's `metadata.key` is automatically updated to reflect the transposition (e.g. `"F# major"` after `+3`). The service uses flat note names for accidentals (Db, Eb, Ab, Bb) — G# is stored as Ab, etc.

**MIDI transposition rules:**
- Scans `muse-work/` recursively for `.mid` and `.midi` files.
- Parses MTrk chunks and modifies Note-On (0x9n) and Note-Off (0x8n) events.
- **Channel 9 (drums) is never transposed** — drums are unpitched and shifting their note numbers would change the GM drum map mapping.
- Notes are clamped to [0, 127] to stay within MIDI range.
- All other events (meta, sysex, CC, program change, pitch bend) are preserved byte-for-byte.
- Track length headers remain unchanged — only note byte values differ.

**Agent use case:** A producer experimenting with key runs `muse transpose +3` and immediately has a versioned, reversible pitch shift on the full arrangement. The agent can then run `muse context --json` to confirm the new key before generating new parts that fit the updated harmonic center. The `--dry-run` flag lets agents preview impact before committing, and the `--track` flag lets them scope transposition to a single instrument (e.g. `--track melody`) without shifting the bass or chords.

**Implementation:** `maestro/services/muse_transpose.py` — `parse_interval`, `update_key_metadata`, `transpose_midi_bytes`, `apply_transpose_to_workdir`, `TransposeResult`. CLI: `maestro/muse_cli/commands/transpose.py` — `_transpose_async` (injectable async core), `_print_result` (renderer). Exit codes: 0 success, 1 user error (bad interval, empty workdir), 2 outside repo, 3 internal error.

> **Section filter note:** `--section TEXT` is accepted by the CLI and logged as a warning but not yet applied. Full section-scoped transposition requires section boundary markers embedded in committed MIDI metadata — tracked as a follow-up enhancement.

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

### `muse rebase`

**Purpose:** Rebase commits onto a new base, producing a linear history. Given a current branch that has diverged from `<upstream>`, `muse rebase <upstream>` collects all commits since the divergence point and replays them one-by-one on top of the upstream tip — each producing a new commit ID with the same snapshot delta. An AI agent uses this to linearise a sequence of late-night fixup commits before merging to main, making the musical narrative readable and bisectable.

**Usage:**
```bash
muse rebase <upstream> [OPTIONS]
muse rebase --continue
muse rebase --abort
```

**Flags:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `UPSTREAM` | positional | — | Branch name or commit ID to rebase onto. Omit with `--continue` / `--abort`. |
| `--interactive` / `-i` | flag | off | Open `$EDITOR` with a rebase plan (pick/squash/drop per commit) before executing. |
| `--autosquash` | flag | off | Automatically move `fixup! <msg>` commits immediately after their matching target commit. |
| `--rebase-merges` | flag | off | Preserve merge commits during replay (experimental). |
| `--continue` | flag | off | Resume a rebase that was paused by a conflict. |
| `--abort` | flag | off | Cancel the in-progress rebase and restore the branch to its original HEAD. |

**Output example (linear rebase):**
```
✅ Rebased 3 commit(s) onto 'dev' [main a1b2c3d4]
```

**Output example (conflict):**
```
❌ Conflict while replaying c2d3e4f5 ('Add strings'):
    both modified: tracks/strings.mid
Resolve conflicts, then run 'muse rebase --continue'.
```

**Output example (abort):**
```
✅ Rebase aborted. Branch 'main' restored to deadbeef.
```

**Interactive plan format:**
```
# Interactive rebase plan.
# Actions: pick, squash (fold into previous), drop (skip), fixup (squash no msg), reword
# Lines starting with '#' are ignored.

pick a1b2c3d4 Add piano
squash b2c3d4e5 Tweak piano velocity
drop c3d4e5f6 Stale WIP commit
pick d4e5f6a7 Add strings
```

**Result type:** `RebaseResult` (dataclass, frozen) — fields:
- `branch` (str): The branch that was rebased.
- `upstream` (str): The upstream branch or commit ref.
- `upstream_commit_id` (str): Resolved commit ID of the upstream tip.
- `base_commit_id` (str): LCA commit where the histories diverged.
- `replayed` (tuple[RebaseCommitPair, ...]): Ordered list of (original, new) commit ID pairs.
- `conflict_paths` (tuple[str, ...]): Conflicting paths (empty on clean completion).
- `aborted` (bool): True when `--abort` cleared the in-progress rebase.
- `noop` (bool): True when there were no commits to replay.
- `autosquash_applied` (bool): True when `--autosquash` reordered commits.

**State file:** `.muse/REBASE_STATE.json` — written on conflict; cleared on `--continue` completion or `--abort`. Contains: `upstream_commit`, `base_commit`, `original_branch`, `original_head`, `commits_to_replay`, `current_onto`, `completed_pairs`, `current_commit`, `conflict_paths`.

**Agent use case:** An agent that maintains a feature branch can call `muse rebase dev` before opening a merge request. If conflicts are detected, the agent receives the conflict paths in `REBASE_STATE.json`, resolves them by picking the correct version of each affected file, then calls `muse rebase --continue`. The `--autosquash` flag is useful after a generation loop that emits intermediate `fixup!` commits — the agent can clean up history automatically before finalising.

**Algorithm:**
1. LCA of HEAD and upstream (via BFS over the commit graph).
2. Collect commits on the current branch since the LCA (oldest first).
3. For each commit, compute its snapshot delta relative to its own parent.
4. Apply the delta onto the current onto-tip manifest; detect conflicts.
5. On conflict: write `REBASE_STATE.json` and exit 1 (await `--continue`).
6. On success: insert a new commit record; advance the onto pointer.
7. After all commits: write the final commit ID to the branch ref.

**Implementation:** `maestro/muse_cli/commands/rebase.py` (Typer CLI), `maestro/services/muse_rebase.py` (`_rebase_async`, `_rebase_continue_async`, `_rebase_abort_async`, `RebaseResult`, `RebaseState`, `InteractivePlan`, `compute_delta`, `apply_delta`, `apply_autosquash`).

---

### `muse stash`

**Purpose:** Temporarily shelve uncommitted muse-work/ changes so the producer can switch context without losing work-in-progress. Push saves the current working state into a filesystem stack (`.muse/stash/`) and restores HEAD; pop brings it back. An AI agent uses this when it needs to checkpoint partial generation state, switch to a different branch task, then resume exactly where it left off.

**Usage:**
```bash
muse stash [push] [OPTIONS]      # save + restore HEAD (default subcommand)
muse stash push [OPTIONS]        # explicit push
muse stash pop [stash@{N}]       # apply + drop most recent entry
muse stash apply [stash@{N}]     # apply without dropping
muse stash list                  # list all entries
muse stash drop [stash@{N}]      # remove a specific entry
muse stash clear [--yes]         # remove all entries
```

**Flags (push):**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--message / -m TEXT` | string | `"On <branch>: stash"` | Label for this stash entry |
| `--track TEXT` | string | — | Scope to `tracks/<track>/` paths only |
| `--section TEXT` | string | — | Scope to `sections/<section>/` paths only |

**Flags (clear):**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--yes / -y` | flag | off | Skip confirmation prompt |

**Output example (push):**
```
Saved working directory and index state stash@{0}
On main: half-finished chorus rearrangement
```

**Output example (pop):**
```
✅ Applied stash@{0}: On main: half-finished chorus rearrangement
   3 file(s) restored.
Dropped stash@{0}
```

**Output example (list):**
```
stash@{0}: On main: WIP chorus changes
stash@{1}: On main: drums experiment
```

**Result types:**

`StashPushResult` (dataclass, frozen) — fields:
- `stash_ref` (str): Human label (e.g. `"stash@{0}"`); empty string when nothing was stashed.
- `message` (str): Label stored in the entry.
- `branch` (str): Branch name at the time of push.
- `files_stashed` (int): Number of files saved into the stash.
- `head_restored` (bool): Whether HEAD snapshot was restored to muse-work/.
- `missing_head` (tuple[str, ...]): Paths that could not be restored from the object store after push.

`StashApplyResult` (dataclass, frozen) — fields:
- `stash_ref` (str): Human label of the entry that was applied.
- `message` (str): The entry's label.
- `files_applied` (int): Number of files written to muse-work/.
- `missing` (tuple[str, ...]): Paths whose object bytes were absent from the store.
- `dropped` (bool): True when the entry was removed (pop); False for apply.

`StashEntry` (dataclass, frozen) — fields:
- `stash_id` (str): Unique filesystem stem.
- `index` (int): Position in the stack (0 = most recent).
- `branch` (str): Branch at the time of stash.
- `message` (str): Human label.
- `created_at` (str): ISO-8601 timestamp.
- `manifest` (dict[str, str]): `{rel_path: sha256_object_id}` of stashed files.
- `track` (str | None): Track scope used during push (or None).
- `section` (str | None): Section scope used during push (or None).

**Storage:** Filesystem-only. Each entry is a JSON file in `.muse/stash/stash-<timestamp>-<uuid8>.json`. File content is preserved in the existing `.muse/objects/<oid[:2]>/<oid[2:]>` content-addressed blob store (same layout as `muse commit` and `muse reset --hard`). No Postgres rows are written.

**Agent use case:** An AI composition agent mid-generation on the chorus wants to quickly address a client request on the intro. It calls `muse stash` to save the in-progress chorus state (files + object blobs), then `muse checkout intro-branch` to switch context, makes the intro fix, then returns and calls `muse stash pop` to restore the chorus work exactly as it was. For scoped saves, `--track drums` limits the stash to drum files only, leaving other tracks untouched in muse-work/.

**Conflict strategy on apply:** Last-write-wins. Files in muse-work/ not in the stash manifest are left untouched. Files whose objects are missing from the store are reported in `missing` but do not abort the operation.

**Stack ordering:** `stash@{0}` is always the most recently pushed entry. `stash@{N}` refers to the Nth entry in reverse chronological order. Multiple `push` calls build a stack; `pop` always takes from the top.

**Implementation:** `maestro/muse_cli/commands/stash.py` (Typer CLI with subcommands), `maestro/services/muse_stash.py` (`push_stash`, `apply_stash`, `list_stash`, `drop_stash`, `clear_stash`, result types).

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

### `muse blame`

**Purpose:** Annotate each tracked file with the commit that last changed it.
Answers the producer's question "whose idea was this bass line?" or "which take
introduced this change?" Output is per-file (not per-line) because MIDI and
audio files are binary — the meaningful unit of change is the whole file.

**Usage:**
```bash
muse blame [PATH] [OPTIONS]
```

**Flags:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `PATH` | positional string | — | Relative path within `muse-work/` to annotate. Omit to blame all tracked files |
| `--track TEXT` | string | — | Filter to files whose basename matches this fnmatch glob (e.g. `bass*` or `*.mid`) |
| `--section TEXT` | string | — | Filter to files inside this section directory (first directory component) |
| `--line-range N,M` | string | — | Annotate sub-range (informational only — MIDI/audio are binary, not line-based) |
| `--json` | flag | off | Emit structured JSON for agent consumption |

**Output example (text):**
```
a1b2c3d4  producer             2026-02-27 14:30:00  (  modified)  muse-work/bass/bassline.mid
    update bass groove
f9e8d7c6  producer             2026-02-26 10:00:00  (     added)  muse-work/keys/melody.mid
    initial take
```

**Output example (`--json`):**
```json
{
  "path_filter": null,
  "track_filter": null,
  "section_filter": null,
  "line_range": null,
  "entries": [
    {
      "path": "muse-work/bass/bassline.mid",
      "commit_id": "a1b2c3d4e5f6...",
      "commit_short": "a1b2c3d4",
      "author": "producer",
      "committed_at": "2026-02-27 14:30:00",
      "message": "update bass groove",
      "change_type": "modified"
    }
  ]
}
```

**Result type:** `BlameEntry` (TypedDict) — fields: `path` (str), `commit_id` (str),
`commit_short` (str, 8-char), `author` (str), `committed_at` (str, `YYYY-MM-DD HH:MM:SS`),
`message` (str), `change_type` (str: `"added"` | `"modified"` | `"unchanged"`).
Wrapped in `BlameResult` (TypedDict) — fields: `path_filter`, `track_filter`,
`section_filter`, `line_range` (all `str | None`), `entries` (list of `BlameEntry`).
See `docs/reference/type_contracts.md § Muse CLI Types`.

**Agent use case:** An AI composing a new bass arrangement asks `muse blame --track 'bass*' --json`
to find the commit that last changed every bass file. It then calls `muse show <commit_id>` on
those commits to understand what musical choices were made, before deciding whether to build on
or diverge from the existing groove.

**Implementation:** `maestro/muse_cli/commands/blame.py` —
`BlameEntry` (TypedDict), `BlameResult` (TypedDict), `_load_commit_chain()`,
`_load_snapshot_manifest()`, `_matches_filters()`, `_blame_async()`, `_render_blame()`.
Exit codes: 0 success, 2 outside repo, 3 internal error.

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

### `muse emotion-diff`

**Purpose:** Compare emotion vectors between two commits to track how the emotional character of a composition changed over time. An AI agent uses this to detect whether a recent edit reinforced or subverted the intended emotional arc, and to decide whether to continue or correct the creative direction.

**Status:** ✅ Implemented (issue #100)

**Usage:**
```bash
muse emotion-diff [COMMIT_A] [COMMIT_B] [OPTIONS]
```

**Flags:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `COMMIT_A` | positional | `HEAD~1` | Baseline commit ref (full hash, abbreviated hash, `HEAD`, or `HEAD~N`). |
| `COMMIT_B` | positional | `HEAD` | Target commit ref. |
| `--track TEXT` | option | — | Scope analysis to a specific track (noted in output; full per-track MIDI scoping is a follow-up). |
| `--section TEXT` | option | — | Scope to a named section (same stub note as `--track`). |
| `--json` | flag | off | Emit structured JSON for agent or tool consumption. |

**Sourcing strategy:**

1. **`explicit_tags`** — Both commits have `emotion:*` tags (set via `muse tag add emotion:<label>`). Vectors are looked up from the canonical emotion table.
2. **`mixed`** — One commit has a tag, the other is inferred from metadata.
3. **`inferred`** — Neither commit has an emotion tag. Vectors are inferred from available commit metadata (tempo, annotations). Full MIDI-feature inference (mode, note density, velocity) is tracked as a follow-up.

**Canonical emotion labels** (for `muse tag add emotion:<label>`):
`joyful`, `melancholic`, `anxious`, `cinematic`, `peaceful`, `dramatic`, `hopeful`, `tense`, `dark`, `euphoric`, `serene`, `epic`, `mysterious`, `aggressive`, `nostalgic`.

**Output example (text):**
```
Emotion diff — a1b2c3d4 → f9e8d7c6
Source: explicit_tags

Commit A (a1b2c3d4):  melancholic
Commit B (f9e8d7c6):  joyful

Dimension      Commit A   Commit B   Delta
-----------    --------   --------   -----
energy           0.3000     0.8000  +0.5000
valence          0.3000     0.9000  +0.6000
tension          0.4000     0.2000  -0.2000
darkness         0.6000     0.1000  -0.5000

Drift: 0.9747
Dramatic emotional departure — a fundamentally different mood. melancholic → joyful (drift=0.975, major, dominant: +valence)
```

**Output example (JSON):**
```json
{
  "commit_a": "a1b2c3d4",
  "commit_b": "f9e8d7c6",
  "source": "explicit_tags",
  "label_a": "melancholic",
  "label_b": "joyful",
  "vector_a": {"energy": 0.3, "valence": 0.3, "tension": 0.4, "darkness": 0.6},
  "vector_b": {"energy": 0.8, "valence": 0.9, "tension": 0.2, "darkness": 0.1},
  "dimensions": [
    {"dimension": "energy",   "value_a": 0.3, "value_b": 0.8, "delta":  0.5},
    {"dimension": "valence",  "value_a": 0.3, "value_b": 0.9, "delta":  0.6},
    {"dimension": "tension",  "value_a": 0.4, "value_b": 0.2, "delta": -0.2},
    {"dimension": "darkness", "value_a": 0.6, "value_b": 0.1, "delta": -0.5}
  ],
  "drift": 0.9747,
  "narrative": "...",
  "track": null,
  "section": null
}
```

**Result types:** `EmotionDiffResult`, `EmotionVector`, `EmotionDimDelta` — all defined in `maestro/services/muse_emotion_diff.py` and registered in `docs/reference/type_contracts.md`.

**Drift distance:** Euclidean distance in the 4-D emotion space. Range [0.0, 2.0].
- < 0.05 — unchanged
- 0.05–0.25 — subtle shift
- 0.25–0.50 — moderate shift
- 0.50–0.80 — significant shift
- > 0.80 — major / dramatic departure

**Agent use case:** An AI composing a new verse calls `muse emotion-diff HEAD~1 HEAD --json` after each commit to verify the composition is tracking toward the intended emotional destination (e.g., building from `melancholic` to `hopeful` across an album arc). A drift > 0.5 on the wrong dimension triggers a course-correction prompt.

**Implementation:** `maestro/muse_cli/commands/emotion_diff.py` (CLI entry point) and `maestro/services/muse_emotion_diff.py` (core engine). All DB-touching paths are async (`open_session()` pattern). Commit refs support `HEAD`, `HEAD~N`, full 64-char hashes, and 8-char abbreviated hashes.

---

### Command Registration Summary

| Command | File | Status | Issue |
|---------|------|--------|-------|
| `muse dynamics` | `commands/dynamics.py` | ✅ stub (PR #130) | #120 |
| `muse emotion-diff` | `commands/emotion_diff.py` | ✅ implemented (PR #100) | #100 |
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

## `muse rev-parse` — Resolve a Revision Expression to a Commit ID

**Purpose:** Translate a symbolic revision expression into a concrete 64-character
commit ID.  Mirrors `git rev-parse` semantics and is the plumbing primitive used
internally by other Muse commands that accept revision arguments.

```
muse rev-parse <revision> [OPTIONS]

```

### Flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `REVISION` | positional | required | Revision expression to resolve |
| `--short` | flag | off | Print only the first 8 characters of the commit ID |
| `--verify` | flag | off | Exit 1 if the expression does not resolve (default: print nothing) |
| `--abbrev-ref` | flag | off | Print the branch name instead of the commit ID |

### Supported Revision Expressions

| Expression | Resolves to |
|------------|-------------|
| `HEAD` | Tip of the current branch |
| `<branch>` | Tip of the named branch |
| `<commit_id>` | Exact or prefix-matched commit |
| `HEAD~N` | N parents back from HEAD |
| `<branch>~N` | N parents back from the branch tip |

### Output Example

```
$ muse rev-parse HEAD
a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2

$ muse rev-parse --short HEAD
a1b2c3d4

$ muse rev-parse --abbrev-ref HEAD
main

$ muse rev-parse HEAD~2
f9e8d7c6b5a4f9e8d7c6b5a4f9e8d7c6b5a4f9e8d7c6b5a4f9e8d7c6b5a4f9e8

$ muse rev-parse --verify nonexistent
fatal: Not a valid revision: 'nonexistent'
# exit code 1
```

### Result Type

`RevParseResult` — see `docs/reference/type_contracts.md § Muse rev-parse Types`.

### Agent Use Case

An AI agent resolves `HEAD~1` before generating a new variation to obtain the
parent commit ID, which it passes as a `base_commit` argument to downstream
commands.  Use `--verify` in automation scripts to fail fast rather than
silently producing empty output.

---

## `muse symbolic-ref` — Read or Write a Symbolic Ref

**Purpose:** Read or write a symbolic ref (e.g. `HEAD`), answering "which branch
is currently checked out?" — the primitive that all checkout, branch, and HEAD
management operations depend on.

**Implementation:** `maestro/muse_cli/commands/symbolic_ref.py`\
**Status:** ✅ implemented (issue #93)

### Usage

```bash
muse symbolic-ref HEAD                         # read: prints refs/heads/main
muse symbolic-ref --short HEAD                 # read short form: prints main
muse symbolic-ref HEAD refs/heads/feature/x   # write: update .muse/HEAD
muse symbolic-ref --delete HEAD               # delete the symbolic ref file

```

### Flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `<name>` | positional | required | Ref name, e.g. `HEAD` or `refs/heads/main` |
| `<ref>` | positional | none | When supplied, write this target into the ref (must start with `refs/`) |
| `--short` | flag | off | Print just the branch name (`main`) instead of the full ref path |
| `--delete / -d` | flag | off | Delete the symbolic ref file entirely |
| `--quiet / -q` | flag | off | Suppress error output when the ref is not symbolic |

### Output example

```
# Read
refs/heads/main

# Read --short
main

# Write
✅ HEAD → refs/heads/feature/guitar

# Delete
✅ Deleted symbolic ref 'HEAD'
```

### Result type

`SymbolicRefResult` — fields: `name` (str), `ref` (str), `short` (str).

### Agent use case

An AI agent inspecting the current branch before generating new variations calls
`muse symbolic-ref --short HEAD` to confirm it is operating on the expected branch.
Before creating a new branch it calls `muse symbolic-ref HEAD refs/heads/feature/guitar`
to update the HEAD pointer atomically. These are pure filesystem operations — no DB
round-trip, sub-millisecond latency.

### Error handling

| Scenario | Exit code | Message |
|----------|-----------|---------|
| Ref file does not exist | 1 (USER_ERROR) | `❌ HEAD is not a symbolic ref or does not exist` |
| Ref content is a bare SHA (detached HEAD) | 1 (USER_ERROR) | same |
| `<ref>` does not start with `refs/` | 1 (USER_ERROR) | `❌ Invalid symbolic-ref target '…': must start with 'refs/'` |
| `--delete` on absent file | 1 (USER_ERROR) | `❌ HEAD: not found — nothing to delete` |
| Not in a repo | 2 (REPO_NOT_FOUND) | Standard `require_repo()` message |

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

## `muse motif` — Recurring Melodic Motif Analysis

### `muse motif find`

**Purpose:** Detect recurring melodic and rhythmic patterns in a single commit.
An AI agent uses this before generating a new variation to identify the established
motific language of the composition, ensuring new material is thematically coherent.

**Usage:**
```bash
muse motif find [<commit>] [OPTIONS]
```

**Flags:**
| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--min-length N` | int | 3 | Minimum motif length in notes |
| `--track TEXT` | str | — | Restrict to a named MIDI track |
| `--section TEXT` | str | — | Restrict to a named section/region |
| `--json` | flag | off | Emit structured JSON for agent consumption |

**Output example:**
```
Recurring motifs — commit a1b2c3d4  (HEAD -> main)
── stub mode: full MIDI analysis pending ──

#  Fingerprint            Contour             Count
-  ----------------------  ------------------  -----
1  [+2, +2, -1, +2]       ascending-step          3
2  [-2, -2, +1, -2]       descending-step         2
3  [+4, -2, +3]           arch                    2

3 motif(s) found (min-length 3)
```

**Result type:** `MotifFindResult` — fields: `commit_id`, `branch`, `min_length`, `motifs` (list of `MotifGroup`), `total_found`, `source`.

**Agent use case:** Call `muse motif find --json HEAD` before composing a new section.
Parse `motifs[0].fingerprint` to retrieve the primary interval sequence, then instruct
the generation model to build on that pattern rather than introducing unrelated material.

**Implementation note:** Stub — returns realistic placeholder motifs. Full
implementation requires MIDI note data queryable from the commit snapshot store.

---

### `muse motif track`

**Purpose:** Search all commits in branch history for appearances of a specific motif.
Detects not only exact transpositions but also melodic inversion, retrograde, and
retrograde-inversion — the four canonical classical transformations.

**Usage:**
```bash
muse motif track "<pattern>" [OPTIONS]
```

**Arguments:**
| Argument | Description |
|----------|-------------|
| `pattern` | Space-separated note names (`"C D E G"`) or MIDI numbers (`"60 62 64 67"`) |

**Flags:**
| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--json` | flag | off | Emit structured JSON |

**Output example:**
```
Tracking motif: 'C D E G'
Fingerprint: [+2, +2, +3]
Commits scanned: 12

Commit      Track         Transform       Position
----------  ------------  --------------  --------
a1b2c3d4    melody        exact                  0
f4e3d2c1    melody        inversion              4
b2c3d4e5    bass          retrograde             2

3 occurrence(s) found.
```

**Result type:** `MotifTrackResult` — fields: `pattern`, `fingerprint`, `occurrences` (list of `MotifOccurrence`), `total_commits_scanned`, `source`.

**Agent use case:** When the agent needs to understand how a theme has evolved,
call `muse motif track "C D E G" --json` and inspect the `transformation` field
on each occurrence to chart the motif's journey through the composition's history.

---

### `muse motif diff`

**Purpose:** Show how the dominant motif transformed between two commits.
Classifies the change as one of: exact (transposition), inversion, retrograde,
retrograde-inversion, augmentation, diminution, or approximate.

**Usage:**
```bash
muse motif diff <commit-a> <commit-b> [OPTIONS]
```

**Flags:**
| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--json` | flag | off | Emit structured JSON |

**Output example:**
```
Motif diff: a1b2c3d4 → f4e3d2c1

  A (a1b2c3d4): [+2, +2, -1, +2]  [ascending-step]
  B (f4e3d2c1): [-2, -2, +1, -2]  [descending-step]

Transformation: INVERSION
The motif was inverted — ascending intervals became descending.
```

**Result type:** `MotifDiffResult` — fields: `commit_a` (`MotifDiffEntry`), `commit_b` (`MotifDiffEntry`), `transformation` (`MotifTransformation`), `description`, `source`.

**Agent use case:** Use after detecting a structural change to understand whether
the composer inverted or retrogressed the theme — crucial context for deciding
how to develop material in the next variation.

---

### `muse motif list`

**Purpose:** List all named motifs saved in `.muse/motifs/`. Named motifs are
user-annotated melodic ideas that the composer has labelled for future recall.

**Usage:**
```bash
muse motif list [OPTIONS]
```

**Flags:**
| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--json` | flag | off | Emit structured JSON |

**Output example:**
```
Named motifs:

Name                  Fingerprint             Created                   Description
--------------------  ----------------------  ------------------------  ------------------------------
main-theme            [+2, +2, -1, +2]        2026-01-15T10:30:00Z      The central ascending motif…
bass-riff             [-2, -3, +2]            2026-01-20T14:15:00Z      Chromatic bass figure…
```

**Result type:** `MotifListResult` — fields: `motifs` (list of `SavedMotif`), `source`.

**Agent use case:** Load the named motif library at session start. Cross-reference
`fingerprint` values against `muse motif find` output to check whether detected
patterns match known named motifs before introducing new thematic material.
---

---

## `muse read-tree` — Read a Snapshot into muse-work/

**Purpose:** Hydrate `muse-work/` from any historical snapshot without modifying
HEAD or branch refs. The plumbing analog of `git read-tree`. AI agents use this to
inspect or restore a specific composition state before making decisions.

**Usage:**
```bash
muse read-tree <snapshot_id> [OPTIONS]
```

**Flags:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `<snapshot_id>` | positional | — | Full 64-char snapshot SHA or abbreviated prefix (≥ 4 chars) |
| `--dry-run` | flag | off | Print the file list without writing anything |
| `--reset` | flag | off | Clear all files from muse-work/ before populating |

**Output example (default):**
```
✅ muse-work/ populated from snapshot a3f7b891 (3 file(s)).
```

**Output example (`--dry-run`):**
```
Snapshot a3f7b891 — 3 file(s):
  tracks/bass/groove.mid  (1a2b3c4d)
  tracks/keys/voicing.mid (9e8f7a6b)
  mix/final.json          (c4d5e6f7)
```

**Output example (`--reset`):**
```
✅ muse-work/ reset and populated from snapshot a3f7b891 (3 file(s)).
```

**Result type:** `ReadTreeResult` — fields: `snapshot_id` (str), `files_written` (list[str]),
`dry_run` (bool), `reset` (bool).

**How objects are stored:** `muse commit` writes each committed file's raw bytes
into `.muse/objects/<sha256>` (the local content-addressed object store). `muse
read-tree` reads from that store to reconstruct `muse-work/`. If an object is
missing (e.g. the snapshot was pulled from a remote without a local commit), the
command exits with a clear error listing the missing paths.

**Does NOT modify:**
- `.muse/HEAD`
- `.muse/refs/heads/<branch>` (any branch ref)
- The database (read-only command)

**Agent use case:** After `muse pull`, an agent calls `muse read-tree <snapshot_id>`
to materialize a specific checkpoint into `muse-work/` for further analysis (e.g.
running `muse dynamics` or `muse swing`) without advancing the branch pointer. This
is safer than `muse checkout` because it leaves all branch metadata intact.

---
## `muse update-ref` — Write or Delete a Ref (Branch or Tag Pointer)

**Purpose:** Directly update a branch or tag pointer (`refs/heads/*` or `refs/tags/*`)
in the `.muse/` object store.  This is the plumbing primitive scripting agents use when
they need to advance a branch tip, retarget a tag, or remove a stale ref — without going
through a higher-level command like `checkout` or `merge`.

**Implementation:** `maestro/muse_cli/commands/update_ref.py`\
**Status:** ✅ implemented (PR #143) — issue #91

### Usage

```bash
muse update-ref <ref> <new-value> [OPTIONS]
muse update-ref <ref> -d
```

### Flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `<ref>` | positional | required | Fully-qualified ref (e.g. `refs/heads/main`, `refs/tags/v1.0`) |
| `<new-value>` | positional | required (unless `-d`) | Commit ID to write to the ref |
| `--old-value <commit_id>` | string | off | CAS guard — only update if the current ref value matches this commit ID |
| `-d / --delete` | flag | off | Delete the ref file instead of writing it |

### Output example

```
# Standard write
✅ refs/heads/main → 3f9ab2c1

# CAS failure
❌ CAS failure: expected '3f9ab2c1' but found 'a1b2c3d4'. Ref not updated.

# Delete
✅ Deleted ref 'refs/heads/feature'.

# Commit not in DB
❌ Commit 3f9ab2c1 not found in database.
```

### Validation

- **Ref format:** Must start with `refs/heads/` or `refs/tags/`.  Any other prefix exits with `USER_ERROR`.
- **Commit existence:** Before writing, the commit_id is looked up in `muse_cli_commits`.  If absent, exits `USER_ERROR`.
- **CAS (`--old-value`):** Reads the current file contents and compares to the provided value.  Mismatch → `USER_ERROR`, ref unchanged.  Absent ref + any `--old-value` → `USER_ERROR`.
- **Delete (`-d`):** Exits `USER_ERROR` when the ref file does not exist.

### Result type

`None` — this is a write command; output is emitted via `typer.echo`.

### Agent use case

An AI orchestration agent that manages multiple arrangement branches can call
`muse update-ref refs/heads/feature/guitar <commit_id> --old-value <prev_id>`
to atomically advance the branch tip after generating a new variation.  The CAS
guard prevents a race condition when two generation passes complete concurrently —
only the first one wins; the second will receive `USER_ERROR` and retry or backoff.

Use `muse update-ref refs/tags/v1.0 <commit_id>` to mark a production-ready
snapshot with a stable tag pointer that other agents can reference by name.
---

## `muse write-tree` — Write Current Working-Tree as a Snapshot Object

### `muse write-tree`

**Purpose:** Hash all files in `muse-work/`, persist the object and snapshot
rows in the database, and print the `snapshot_id`.  This is the plumbing
primitive that underlies `muse commit` — it writes the tree object without
recording any history (no commit row, no branch-pointer update).  AI agents
use it to obtain a stable, content-addressed handle to the current working-tree
state before deciding whether to commit.

**Status:** ✅ Fully implemented (issue #89)

**Usage:**
```bash
muse write-tree [OPTIONS]
```

**Flags:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--prefix PATH` | string | *(none)* | Only include files whose path (relative to `muse-work/`) starts with *PATH*. Example: `--prefix drums/` snapshots only the drums sub-directory. |
| `--missing-ok` | flag | off | Do not fail when `muse-work/` is absent or empty, or when `--prefix` matches no files. Still prints a valid (empty) `snapshot_id`. |

**Output example:**
```
a3f92c1e8b4d5f67890abcdef1234567890abcdef1234567890abcdef12345678
```
(64-character sha256 hex digest of the sorted `path:object_id` pairs.)

**Idempotency:** Same file content → same `snapshot_id`.  Running `muse write-tree`
twice without changing any file makes exactly zero new DB writes (all upserts are
no-ops).

**Result type:** `snapshot_id` is a raw 64-char hex string printed to stdout.
No named result type — the caller decides what to do with the ID (compare,
commit, discard).

**Agent use case:** An AI agent that just generated a batch of MIDI files calls
`muse write-tree` to get the `snapshot_id` for the current working tree.  It
then compares that ID against the last committed `snapshot_id` (via `muse log
--json | head -1`) to decide whether the new generation is novel enough to
commit.  If the IDs match, the files are identical to the last commit and no
commit is needed.

**Implementation:** `maestro/muse_cli/commands/write_tree.py`.  Reuses
`build_snapshot_manifest`, `compute_snapshot_id`, `upsert_object`, and
`upsert_snapshot` from the commit pipeline.  No new DB schema — the same
`muse_cli_objects` and `muse_cli_snapshots` tables.

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
| `muse read-tree` | `commands/read_tree.py` | ✅ implemented (PR #157) | #90 |
| `muse recall` | `commands/recall.py` | ✅ stub (PR #135) | #122 |
| `muse render-preview` | `commands/render_preview.py` | ✅ implemented (issue #96) | #96 |
| `muse rev-parse` | `commands/rev_parse.py` | ✅ implemented (PR #143) | #92 |
| `muse session` | `commands/session.py` | ✅ implemented (PR #129) | #127 |
| `muse swing` | `commands/swing.py` | ✅ stub (PR #131) | #121 |
| `muse motif` | `commands/motif.py` | ✅ stub (PR —) | #101 |
| `muse symbolic-ref` | `commands/symbolic_ref.py` | ✅ implemented (issue #93) | #93 |
| `muse tag` | `commands/tag.py` | ✅ implemented (PR #133) | #123 |
| `muse tempo-scale` | `commands/tempo_scale.py` | ✅ stub (PR open) | #111 |
| `muse timeline` | `commands/timeline.py` | ✅ implemented (PR #TBD) | #97 |
| `muse transpose` | `commands/transpose.py` | ✅ implemented | #102 |
| `muse update-ref` | `commands/update_ref.py` | ✅ implemented (PR #143) | #91 |
| `muse validate` | `commands/validate.py` | ✅ implemented (PR #TBD) | #99 |
| `muse write-tree` | `commands/write_tree.py` | ✅ implemented | #89 |

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

---

## `muse reset` — Reset Branch Pointer to a Prior Commit

### Purpose

Move the current branch's HEAD pointer backward to a prior commit, with three
levels of aggression mirroring git's model.  The "panic button" for music
production: when a producer makes ten bad commits and wants to return to the
last known-good take.

### Usage

```bash
muse reset [--soft | --mixed | --hard] [--yes] <commit>
```

### Flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `<commit>` | positional | required | Target commit (HEAD, HEAD~N, full/abbreviated SHA) |
| `--soft` | flag | off | Move branch pointer only; muse-work/ and object store unchanged |
| `--mixed` | flag | on | Move branch pointer and reset index (default; equivalent to soft in current model) |
| `--hard` | flag | off | Move branch pointer AND overwrite muse-work/ with target snapshot |
| `--yes` / `-y` | flag | off | Skip confirmation prompt for --hard mode |

### Modes

**Soft** (`--soft`):
- Updates `.muse/refs/heads/<branch>` to point to the target commit.
- `muse-work/` files are completely untouched.
- The next `muse commit` will capture the current working tree on top of the rewound HEAD.
- Use when you want to re-commit with a different message or squash commits.

**Mixed** (`--mixed`, default):
- Same as soft in the current Muse model (no explicit staging area exists yet).
- Included for API symmetry with git and forward-compatibility when a staging
  index is added.

**Hard** (`--hard`):
- Moves the branch ref to the target commit.
- Overwrites every file in `muse-work/` with the content from the target
  commit's snapshot.  Files are read from `.muse/objects/<sha[:2]>/<sha[2:]>`
  (the content-addressed blob store populated by `muse commit`).
- Files in `muse-work/` that are NOT present in the target snapshot are deleted.
- **Prompts for confirmation** unless `--yes` is given — this is destructive.
- **Requires `muse commit` to have been run** at least once after repo init so
  that object blobs are present in `.muse/objects/`.

### HEAD~N Syntax

```bash
muse reset HEAD~1       # one parent back (previous commit)
muse reset HEAD~3       # three parents back
muse reset abc123       # abbreviated SHA prefix
muse reset --hard HEAD~2  # two parents back + restore working tree
```

`HEAD~N` walks the primary parent chain only.  Merge parents
(`parent2_commit_id`) are not traversed.

### Guards

- **Merge in progress**: blocked when `.muse/MERGE_STATE.json` exists.
  Resolve or abort the merge before resetting.
- **No commits on branch**: exits with `USER_ERROR` if the current branch
  has never been committed.
- **Missing object blobs** (hard mode): exits with `INTERNAL_ERROR` rather
  than silently leaving `muse-work/` in a partial state.

### Output Examples

```
# Soft/mixed reset:
✅ HEAD is now at abc123de

# Hard reset (2 files restored, 1 deleted):
✅ HEAD is now at abc123de (2 files restored, 1 files deleted)

# Abort:
⚠️  muse reset --hard will OVERWRITE muse-work/ with the target snapshot.
    All uncommitted changes will be LOST.
Proceed? [y/N]: N
Reset aborted.
```

### Object Store

`muse commit` now writes every file's blob content to `.muse/objects/` using
a two-level sharding layout identical to git's loose-object store:

```
.muse/objects/
  ab/           ← first two hex chars of sha256
    cdef1234...  ← remaining 62 chars — the raw file bytes
```

This store enables `muse reset --hard` to restore any previously committed
snapshot without needing the live `muse-work/` files.  Objects are written
idempotently (never overwritten once stored).

### Result Type

`ResetResult` — see [`docs/reference/type_contracts.md`](../reference/type_contracts.md).

### Agent Use Case

An AI composition agent uses `muse reset` to recover from a bad generation run:

1. Agent calls `muse log --json` to identify the last known-good commit SHA.
2. Agent calls `muse reset --hard --yes <sha>` to restore the working tree.
3. Agent calls `muse status` to verify the working tree matches expectations.
4. Agent resumes composition from the clean baseline.

### Implementation

- **Service layer:** `maestro/services/muse_reset.py` — `perform_reset()`,
  `resolve_ref()`, `store_object()`, `object_store_path()`.
- **CLI command:** `maestro/muse_cli/commands/reset.py` — Typer callback,
  confirmation prompt, error display.
- **Commit integration:** `maestro/muse_cli/commands/commit.py` — calls
  `store_object()` for each file during commit to populate `.muse/objects/`.
- **Exit codes:** 0 success, 1 user error (`USER_ERROR`), 2 not a repo
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

---

### `muse checkout`

**Purpose:** Switch branches or create a new branch seeded from the current HEAD.
Enables the branching workflows that allow composers and AI agents to explore
divergent musical directions without losing prior work.

**Usage:**
```bash
muse checkout <branch>            # Switch to an existing branch
muse checkout -b <new-branch>     # Create branch from HEAD, then switch
```

**Flags:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-b` | flag | off | Create the branch from the current HEAD commit and switch to it |

**Output example (create):**
```
✅ Switched to a new branch 'experiment'
```

**Output example (switch):**
```
✅ Switched to branch 'main' [a1b2c3d4]
```

**Agent use case:** Create an experiment branch before exploring a rhythmically
unusual variation.  If the experiment fails, checkout main and the original
arrangement is untouched.

**Implementation:** `maestro/muse_cli/commands/checkout.py` — `checkout_branch(root, branch, create)`.
Pure filesystem writes: creates/updates `.muse/refs/heads/<branch>` and `.muse/HEAD`.
No DB interaction at checkout time — the DAG remains intact.

---

### `muse restore`

**Purpose:** Restore specific files from a commit or index into `muse-work/` without
touching the branch pointer.  Surgical alternative to `muse reset --hard` — bring
back "the bass from take 3" while keeping everything else at HEAD.

**Usage:**
```bash
muse restore <paths>...                          # restore from HEAD (default)
muse restore --staged <paths>...                 # restore index entry from HEAD
muse restore --source <commit> <paths>...        # restore from a specific commit
muse restore --worktree --source <commit> <paths>...  # explicit worktree restore
```

**Flags:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `<paths>...` | positional | — | One or more relative paths within `muse-work/` to restore. Accepts paths with or without the `muse-work/` prefix. |
| `--staged` | flag | off | Restore the index (snapshot manifest) entry from the source commit. In the current Muse model (no separate staging area) this is equivalent to `--worktree`. |
| `--worktree` | flag | off | Restore `muse-work/` files from the source snapshot. Default when no mode flag is specified. |
| `--source / -s` | str | HEAD | Commit reference to restore from: `HEAD`, `HEAD~N`, full SHA, or any unambiguous SHA prefix. |

**Output example:**
```
✅ Restored 'bass/bassline.mid' from commit ab12cd34
```

Multiple files:
```
✅ Restored 2 files from commit ab12cd34:
   • bass/bassline.mid
   • drums/kick.mid
```

**Result type:** `RestoreResult` — fields: `source_commit_id` (str), `paths_restored` (list[str]), `staged` (bool).

**Error cases:**
- `PathNotInSnapshotError` — the requested path does not exist in the source commit's snapshot. Exit code 1.
- `MissingObjectError` — the required blob is absent from `.muse/objects/`. Exit code 3.
- Unknown `--source` ref — exits with code 1 and a clear error message.

**Agent use case:** An AI composition agent can selectively restore individual
instrument tracks from historical commits.  For example, after generating several
takes, the agent can restore the best bass line from take 3 while keeping drums
and keys from take 7 — without modifying the branch history.  Use `muse log` to
identify commit SHAs, then `muse show <commit>` to inspect the snapshot manifest
before running `muse restore`.

**Implementation:** `maestro/muse_cli/commands/restore.py` (CLI) and
`maestro/services/muse_restore.py` (service).  Uses the same object store helpers
as `muse reset --hard`.  Branch pointer is never modified.

---

### `muse resolve`

**Purpose:** Mark a conflicted file as resolved during a paused `muse merge`.
Called after `muse merge` exits with a conflict to accept one side's version
before running `muse merge --continue`.  For `--theirs`, the command
automatically fetches the incoming branch's object from the local store and
writes it to `muse-work/<path>` — no manual file editing required.

**Usage:**
```bash
muse resolve <file-path> --ours    # Keep current branch's working-tree version (no file change)
muse resolve <file-path> --theirs  # Copy incoming branch's object to muse-work/ automatically
```

**Flags:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--ours` | flag | off | Accept the current branch's version (no file change needed) |
| `--theirs` | flag | off | Fetch the incoming branch's object from local store and write to muse-work/ |

**Output example:**
```
✅ Resolved 'meta/section-1.json' — keeping theirs
   1 conflict(s) remaining. Resolve all, then run 'muse merge --continue'.
✅ Resolved 'beat.mid' — keeping ours
✅ All conflicts resolved. Run 'muse merge --continue' to create the merge commit.
```

**Full conflict resolution workflow:**
```bash
muse merge experiment          # → conflict on beat.mid
muse status                    # → shows "You have unmerged paths"
muse resolve beat.mid --theirs # → copies theirs version into muse-work/
muse merge --continue          # → creates merge commit, clears MERGE_STATE.json
```

**Note:** After all conflicts are resolved, `.muse/MERGE_STATE.json` persists
with `conflict_paths=[]` so `--continue` can read the stored commit IDs.
`muse merge --continue` is the command that clears MERGE_STATE.json.
If the theirs object is not in the local store (e.g. branch was never
committed locally), run `muse pull` first to fetch remote objects.

**Implementation:** `maestro/muse_cli/commands/resolve.py` — `resolve_conflict_async(file_path, ours, root, session)`.
Reads and rewrites `.muse/MERGE_STATE.json`.  For `--theirs`, queries DB for
the theirs commit's snapshot manifest and calls `apply_resolution()` from
`merge_engine.py` to restore the file from the local object store.

---

### `muse merge --continue`

**Purpose:** Finalize a merge that was paused due to file conflicts.  After all
conflicts are resolved via `muse resolve`, this command creates the merge commit
with two parent IDs and advances the branch pointer.

**Usage:**
```bash
muse merge --continue
```

**Flags:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--continue` | flag | off | Finalize a paused conflicted merge |

**Output example:**
```
✅ Merge commit [main a1b2c3d4] — merged 'experiment' into 'main'
```

**Contract:** Reads `.muse/MERGE_STATE.json` for commit IDs.  Fails if any
`conflict_paths` remain (use `muse resolve` first).  Snapshots the current
`muse-work/` contents as the merged state.  Clears MERGE_STATE.json on success.

**Agent use case:** After resolving a harmonic conflict between two branches,
run `--continue` to record the merged arrangement as an immutable commit.

**Implementation:** `maestro/muse_cli/commands/merge.py` — `_merge_continue_async(root, session)`.

---

### `muse merge --abort`

**Purpose:** Cancel an in-progress merge and restore the pre-merge state of all
conflicted files.  Use when a conflict is too complex to resolve and you want to
return the working tree to the clean state it was in before `muse merge` ran.

**Usage:**
```bash
muse merge --abort
```

**Flags:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--abort` | flag | off | Cancel the in-progress merge and restore pre-merge files |

**Output example:**
```
✅ Merge abort. Restored 2 conflicted file(s).
```

**Contract:**
- Reads `.muse/MERGE_STATE.json` for `ours_commit` and `conflict_paths`.
- Fetches the ours commit's snapshot manifest from DB.
- For each conflicted path: restores the ours version from the local object
  store to `muse-work/`.  Paths that existed only on the theirs branch (not
  in ours manifest) are deleted from `muse-work/`.
- Clears `.muse/MERGE_STATE.json` on success.
- Exits 1 if no merge is in progress.

**Agent use case:** When an AI agent detects an irresolvable semantic conflict
(e.g. two structural arrangements that cannot be combined), it should call
`muse merge --abort` to restore a clean baseline before proposing an
alternative strategy to the user.

**Implementation:** `maestro/muse_cli/commands/merge.py` — `_merge_abort_async(root, session)`.
Queries DB for the ours commit's manifest, then calls `apply_resolution()` from
`merge_engine.py` for each conflicted path.

---

### `muse release`

**Purpose:** Export a tagged commit as distribution-ready release artifacts — the
music-native publish step.  Bridges the Muse VCS world and the audio production
world: a producer says "version 1.0 is done" and `muse release v1.0` produces
WAV/MIDI/stem files with SHA-256 checksums for distribution.

**Usage:**
```bash
muse release <tag> [OPTIONS]
```

**Flags:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `<tag>` | positional | required | Tag string (created via `muse tag add`) or short commit SHA prefix |
| `--render-audio` | flag | off | Render all MIDI to a single audio file via Storpheus |
| `--render-midi` | flag | off | Bundle all .mid files into a zip archive |
| `--export-stems` | flag | off | Export each instrument track as a separate audio file |
| `--format wav\|mp3\|flac` | option | `wav` | Audio output format |
| `--output-dir PATH` | option | `./releases/<tag>/` | Destination directory for all artifacts |
| `--json` | flag | off | Emit structured JSON for agent consumption |

**Output layout:**
```
<output-dir>/
    release-manifest.json        # always written; SHA-256 checksums
    audio/<commit8>.<format>     # --render-audio
    midi/midi-bundle.zip         # --render-midi
    stems/<stem>.<format>        # --export-stems
```

**Output example:**
```
✅ Release artifacts for tag 'v1.0' (commit a1b2c3d4):
   [audio] ./releases/v1.0/audio/a1b2c3d4.wav
   [midi-bundle] ./releases/v1.0/midi/midi-bundle.zip
   [manifest] ./releases/v1.0/release-manifest.json
⚠️  Audio files are MIDI stubs (Storpheus /render endpoint not yet deployed).
```

**Result type:** `ReleaseResult` — fields: `tag`, `commit_id`, `output_dir`,
`manifest_path`, `artifacts` (list of `ReleaseArtifact`), `audio_format`, `stubbed`.

**`release-manifest.json` shape:**
```json
{
  "tag": "v1.0",
  "commit_id": "<full sha256>",
  "commit_short": "<8-char>",
  "released_at": "<ISO-8601 UTC>",
  "audio_format": "wav",
  "stubbed": true,
  "files": [
    {"path": "audio/a1b2c3d4.wav", "sha256": "...", "size_bytes": 4096, "role": "audio"},
    {"path": "midi/midi-bundle.zip", "sha256": "...", "size_bytes": 1024, "role": "midi-bundle"},
    {"path": "release-manifest.json", "sha256": "...", "size_bytes": 512, "role": "manifest"}
  ]
}
```

**Agent use case:** An AI music generation agent calls `muse release v1.0 --render-midi --json`
after tagging a completed composition.  It reads `stubbed` from the JSON output to
determine whether the audio files are real renders or MIDI placeholders, and inspects
`files[*].sha256` to verify integrity before uploading to a distribution platform.

**Implementation stub note:** The Storpheus `POST /render` endpoint (MIDI-in → audio-out)
is not yet deployed.  Until it ships, `--render-audio` and `--export-stems` copy the
source MIDI file as a placeholder and set `stubbed=true` in the manifest.  The
`_render_midi_to_audio` function in `maestro/services/muse_release.py` is the only
site to update when the endpoint becomes available.

**Implementation:** `maestro/muse_cli/commands/release.py` — `_release_async(...)`.
Service layer: `maestro/services/muse_release.py` — `build_release(...)`.

---

# .museattributes Reference

`.museattributes` is a per-repository configuration file that declares merge strategies for specific track patterns and musical dimensions. It lives in the repository root, next to `.muse/`.

---

## Purpose

Without `.museattributes`, every musical dimension conflict requires manual resolution, even when the resolution is obvious — for example, the drum tracks are always authoritative and should never be overwritten by a collaborator's edits.

`.museattributes` lets you encode that domain knowledge once, so `muse merge` can skip conflict detection and take the correct side automatically.

---

## File Format

One rule per line:

```
<track-pattern>  <dimension>  <strategy>
```

- **`track-pattern`** — An [`fnmatch`](https://docs.python.org/3/library/fnmatch.html) glob matched against the track name (e.g. `drums/*`, `bass/electric`, `*`).
- **`dimension`** — A musical dimension name or `*` (all dimensions). Valid dimension names: `harmonic`, `rhythmic`, `melodic`, `structural`, `dynamic`.
- **`strategy`** — How to resolve conflicts for matching track + dimension pairs (see table below).

Lines starting with `#` and blank lines are ignored. Tokens are separated by any whitespace. The **first matching rule wins** — order matters.

---

## Strategies

| Strategy | Meaning |
|----------|---------|
| `ours`   | Take the current branch's version. Skip conflict detection. |
| `theirs` | Take the incoming branch's version. Skip conflict detection. |
| `union`  | Attempt to include both sides (falls through to three-way merge). |
| `auto`   | Let the merge engine decide (default when no rule matches). |
| `manual` | Flag this dimension for mandatory manual resolution. Falls through to three-way merge. |

> **Note:** `ours` and `theirs` are the only strategies that bypass conflict detection. All others participate in the normal three-way merge.

---

## Examples

### Drums are always authoritative

```
# Drums are owned by the arranger — always keep ours.
drums/*  *  ours
```

### Accept collaborator's harmonic changes wholesale

```
# Incoming harmonic edits from the collaborator win.
keys/*   harmonic  theirs
bass/*   harmonic  theirs
```

### Explicit per-dimension rules with fallback

```
# Drums: our rhythmic pattern is never overwritten.
drums/*  rhythmic  ours

# Melodic content from the collaborator is accepted.
*        melodic   theirs

# Everything else: normal automatic merge.
*        *         auto
```

### Full example

```
# Percussion is always ours.
drums/*     *         ours
percussion  *         ours

# Harmonic collaborations from the feature branch are accepted.
keys/*      harmonic  theirs
strings/*   harmonic  theirs

# Structural sections require manual sign-off.
*           structural  manual

# Fall through to automatic merge for everything else.
*           *           auto
```

---

## CLI

```
muse attributes [--json]
```

Reads and displays the `.museattributes` rules from the current repository.

**Example output:**

```
.museattributes — 3 rule(s)

Track Pattern  Dimension  Strategy
-------------  ---------  --------
drums/*        *          ours
keys/*         harmonic   theirs
*              *          auto
```

Use `--json` for machine-readable output:

```json
[
  {"track_pattern": "drums/*", "dimension": "*", "strategy": "ours"},
  {"track_pattern": "keys/*", "dimension": "harmonic", "strategy": "theirs"},
  {"track_pattern": "*", "dimension": "*", "strategy": "auto"}
]
```

---

## Dimension Implementation Status

The five dimension names are all valid in `.museattributes` and are parsed correctly. However, not all dimensions are currently wired into `build_merge_result`. The table below shows the current state:

| Dimension | Status | Planned event-type mapping |
|-----------|--------|---------------------------|
| `melodic` | **Reserved — future** | Note pitch / pitch-class resolution |
| `rhythmic` | **Reserved — future** | Note start-beat / duration resolution |
| `harmonic` | **Reserved — future** | Pitch-bend event resolution |
| `dynamic` | **Reserved — future** | CC and aftertouch event resolution |
| `structural` | **Reserved — future** | Section / region-level merge |

> **Current behaviour:** `build_merge_result` performs a pure three-way merge for all event
> types (notes, CC, pitch bends, aftertouch) regardless of any `.museattributes` rules.
> A rule such as `drums/*  rhythmic  ours` is parsed and stored correctly but has **no
> effect on the merge outcome today**. All five dimensions are reserved for a future
> implementation that will wire each event type to its corresponding dimension strategy.
>
> Writing dimension-specific rules is safe — they will take effect automatically once
> the merge engine is updated.

---

## Behaviour During `muse merge`

1. `muse merge` calls `load_attributes(repo_path)` to read the file.
2. `resolve_strategy(attributes, track, dimension)` is available for callers to query the configured strategy for any track + dimension pair.
3. **Dimension strategies are not yet applied inside `build_merge_result`.** All event types currently go through the normal three-way merge regardless of the resolved strategy.
4. When dimension wiring is complete: if the resolved strategy is `ours`, the left (current) snapshot will be taken without conflict detection; if `theirs`, the right (incoming) snapshot will be taken; all other strategies (`union`, `auto`, `manual`) will fall through to the three-way merge.

---

## Resolution Precedence

Rules are evaluated top-to-bottom. The first rule whose `track-pattern` **and** `dimension` both match (using fnmatch) wins. If no rule matches, `auto` is used.

---

## Notes

- The file is optional. If `.museattributes` does not exist, `muse merge` behaves as if all dimensions use `auto`.
- Track names typically follow the format `<family>/<instrument>` (e.g. `drums/kick`, `bass/electric`). The exact names depend on your project's MIDI track naming.
- `ours` and `theirs` in `.museattributes` are **positional**, not branch-named. `ours` = the branch you are merging **into** (left / current HEAD). `theirs` = the branch you are merging **from** (right / incoming).

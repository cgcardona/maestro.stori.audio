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

## Behaviour During `muse merge`

1. `muse merge` calls `load_attributes(repo_path)` to read the file.
2. For each region in the merge, the region's track name and dimension are passed to `resolve_strategy(attributes, track, dimension)`.
3. If the resolved strategy is `ours`, the left (current) snapshot is taken without conflict detection.
4. If the resolved strategy is `theirs`, the right (incoming) snapshot is taken without conflict detection.
5. For all other strategies (`union`, `auto`, `manual`), the normal three-way merge runs and may produce conflicts.

---

## Resolution Precedence

Rules are evaluated top-to-bottom. The first rule whose `track-pattern` **and** `dimension` both match (using fnmatch) wins. If no rule matches, `auto` is used.

---

## Notes

- The file is optional. If `.museattributes` does not exist, `muse merge` behaves as if all dimensions use `auto`.
- Track names typically follow the format `<family>/<instrument>` (e.g. `drums/kick`, `bass/electric`). The exact names depend on your project's MIDI track naming.
- `ours` and `theirs` in `.museattributes` are **positional**, not branch-named. `ours` = the branch you are merging **into** (left / current HEAD). `theirs` = the branch you are merging **from** (right / incoming).

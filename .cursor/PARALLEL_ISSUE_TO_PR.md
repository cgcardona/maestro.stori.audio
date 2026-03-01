# Parallel Agent Kickoff — Issue → PR

> ## YOU ARE THE COORDINATOR
>
> If you are an AI agent reading this document, your role is **coordinator only**.
>
> **Your job — the full list, nothing more:**
> 1. Query GitHub for the current phase label to get your canonical batch — **never use a hardcoded list**.
> 2. Pull `dev` to confirm it is up to date.
> 3. Run the Setup script below to create one worktree per issue.
> 4. Launch one sub-agent per worktree by pasting the Kickoff Prompt (found at the bottom of this document) into a separate Cursor composer window rooted in that worktree.
> 5. Report back once all sub-agents have been launched.
>
> **You do NOT:**
> - Check out branches or implement any feature yourself.
> - Run mypy or pytest yourself.
> - Create PRs yourself.
> - Read issue bodies or study code yourself.
> - Hardcode issue numbers — **the GitHub label is the single source of truth**.
>
> The **Kickoff Prompt** at the bottom of this document is for the sub-agents, not for you.
> Copy it verbatim into each sub-agent's window. Do not follow it yourself.

---

Each sub-agent gets its own ephemeral worktree. Worktrees are created at kickoff,
named by issue number, and **deleted by the sub-agent when its job is done**.
The branch and PR live on GitHub regardless — the local worktree is just a
working directory.

---

## Architecture

```
Kickoff (coordinator)
  └─ for each issue:
       DEV_SHA=$(git rev-parse dev)
       git worktree add --detach .../issue-<N> "$DEV_SHA"  ← detached HEAD at dev tip
       write .agent-task into it                            ← task assignment, no guessing
       launch agent in that directory

Agent (per worktree)
  └─ cat .agent-task                        ← knows exactly what to do
  └─ gh pr list --search "closes #<N>"     ← CHECK FIRST: existing PR or branch?
     git ls-remote origin | grep issue-<N>   if found → stop + self-destruct
  └─ git checkout -b feat/<description>     ← creates feature branch (only if new)
  └─ implement → mypy → tests → commit      ← build the fix
  └─ git fetch origin && git merge origin/dev  ← sync dev before pushing
  └─ resolve conflicts if any → re-run mypy + tests
  └─ git push → gh pr create
  └─ git worktree remove --force <path>     ← self-destructs when done
  └─ git worktree prune
```

Worktrees are **not** kept around between cycles. If an agent crashes before
cleanup, run `git worktree prune` from the main repo.

---

## Issue selection — read before choosing

Picking the wrong four issues is the primary source of merge conflicts and wasted
agent cycles. Apply **both** criteria below before finalising your batch.

### Criterion 1 — Foundational first (load-bearing order)

Choose issues whose solutions **unlock or de-risk subsequent work**. A foundational
issue is one where:

- Its output (a new model, endpoint, test fixture, shared utility, or data contract)
  is a dependency that later issues will build on.
- Completing it first means later agents can rely on it rather than reinventing it.
- Deferring it forces future agents to make assumptions that may need to be undone.

**How to identify load-bearing issues:**

1. Look for issues that introduce **shared infrastructure**: new DB models, new API
   routes, new typed result types, new test fixtures, or new config values.
2. Look for issues that **other open issues reference** in their body (`Depends on`,
   `Blocked by`, `Requires`, `See also`).
3. Look for issues whose labels suggest broad impact: `enhancement`, `ai-pipeline`,
   `muse`, `maestro-integration` — these tend to be more foundational than
   `documentation` or `good first issue`.
4. Within a batch of UI issues, prefer the one that establishes the **shared
   component or API pattern** that the others will follow.

Always note the load-bearing order in the Setup script comment so the next
coordinator can read the rationale (e.g., `# Load-bearing order: #A (API contract) → #B (tests) → #C/#D (UI polish)`).

### Criterion 2 — Fully decoupled (zero file overlap)

**Parallel agents can introduce regressions when issues share files.**

Before finalising your four, confirm each pair is independent:

- **Zero file overlap** — two agents must not modify the same file. If they do,
  the second agent's pre-push sync will produce conflicts and risk overwriting
  the first agent's work.
- **No shared schema changes** — Alembic migrations must be sequential. If two
  issues both require a migration, do them in order, not in parallel.
- **No shared config or constant changes** — changes to `maestro/config.py`,
  `maestro/protocol/events.py`, or `_GM_ALIASES` must be serialized.
- **No shared template sections** — two agents editing the same HTML template
  (even different sections) will conflict at merge time. Assign one template per agent.

**How to verify decoupling:**

```bash
# For each candidate issue, list the files it is expected to touch:
gh issue view <N> --json body   # check "Files / modules" section

# Confirm no pair shares a file before assigning the batch.
```

If issues are **dependent** (B cannot ship without A):
1. State it in the issue body: `**Depends on #A** — implement after #A is merged.`
2. Label it `blocked`.
3. Do **not** assign it to a parallel agent until #A is merged.
4. Only then is it safe to run in the next parallel batch.

---

## Setup — run this before launching agents

Run from anywhere inside the main repo. Paths are derived automatically.

> **Critical:** Worktrees use `--detach` at the dev tip SHA — never branch name
> `dev` directly. This prevents the "dev is already used by worktree" error when
> the main repo has `dev` checked out.

> **GitHub repo slug:** Always `cgcardona/maestro`. The local path
> (`/Users/gabriel/dev/tellurstori/maestro`) is misleading — `tellurstori` is
> NOT the GitHub org. Never derive the slug from `basename` or `pwd`.

```bash
REPO=$(git rev-parse --show-toplevel)
PRTREES="$HOME/.cursor/worktrees/$(basename "$REPO")"
mkdir -p "$PRTREES"
cd "$REPO"

# GitHub repo slug — hardcoded. NEVER derive from local path.
GH_REPO=cgcardona/maestro

# Enable rerere so git caches conflict resolutions across agents.
# When multiple agents resolve the same conflict (e.g. muse_vcs.md), rerere
# automatically reuses the recorded resolution — no manual work needed.
# || true: the sandbox blocks .git/config writes (EPERM) when this runs as
# part of a multi-statement block. rerere is an optimization, not critical.
git config rerere.enabled true || true

# ── PHASE LABEL ─────────────────────────────────────────────────────────────
# Change this to the current phase label. This is the ONLY value you update.
PHASE_LABEL="phase-1"

# ── DERIVE BATCH FROM GITHUB — never hardcode issue numbers ─────────────────
# The label on GitHub is the single source of truth. If an issue is labeled
# phase-1 but not in this list, it WILL be skipped and the phase will appear
# complete when it isn't. Always derive from the label.
echo "📋 Querying GitHub for open '$PHASE_LABEL' issues..."
mapfile -t RAW_ISSUES < <(
  gh issue list \
    --repo "$GH_REPO" \
    --label "$PHASE_LABEL" \
    --state open \
    --json number,title \
    --jq '.[] | "\(.number)|\(.title)"'
)

if [ ${#RAW_ISSUES[@]} -eq 0 ]; then
  echo "✅ No open issues with label '$PHASE_LABEL'. Phase is complete."
  exit 0
fi

echo "Found ${#RAW_ISSUES[@]} open issue(s):"
for entry in "${RAW_ISSUES[@]}"; do
  echo "  #${entry%%|*}: ${entry##*|}"
done

# ── ISSUE SELECTION (coordinator applies both criteria before proceeding) ────
# From RAW_ISSUES, select up to 4 that satisfy:
#   Criterion 1 — Foundational first (see Issue selection section above)
#   Criterion 2 — Zero file overlap between any two selected issues
#
# Document your selection and rationale in the comments below, then populate
# SELECTED_ISSUES. Do NOT skip this review — blindly passing all RAW_ISSUES
# to agents causes merge conflicts when issues share files.
#
# Example (replace with your actual selection):
#   #NNN → files: maestro/foo.py, tests/test_foo.py
#   #MMM → files: maestro/bar.py, tests/test_bar.py
#   Load-bearing order: #NNN (shared fixture) → #MMM (consumer)
declare -a SELECTED_ISSUES=(
  # Paste selected entries from RAW_ISSUES here, one per line:
  # "NNN|title of issue NNN"
)

if [ ${#SELECTED_ISSUES[@]} -eq 0 ]; then
  echo "⚠️  SELECTED_ISSUES is empty. Populate it from RAW_ISSUES before running."
  exit 1
fi

# ── SNAPSHOT DEV TIP — all worktrees start here ──────────────────────────────
DEV_SHA=$(git rev-parse dev)

# ── CREATE WORKTREES + TASK FILES ────────────────────────────────────────────
for entry in "${SELECTED_ISSUES[@]}"; do
  NUM="${entry%%|*}"
  TITLE="${entry##*|}"
  WT="$PRTREES/issue-$NUM"
  if [ -d "$WT" ]; then
    echo "⚠️  worktree issue-$NUM already exists, skipping"
    continue
  fi
  git worktree add --detach "$WT" "$DEV_SHA"
  # Assign ROLE based on issue labels:
  #   muse, muse-cli, muse-hub, merge labels → muse-specialist
  #   phase-1/db-schema, alembic, migration labels → database-architect
  #   all others → python-developer
  ISSUE_LABELS=$(gh issue view "$NUM" --repo "$GH_REPO" --json labels --jq '[.labels[].name] | join(",")' 2>/dev/null || echo "")
  AGENT_ROLE="python-developer"
  if echo "$ISSUE_LABELS" | grep -qE "muse-cli|muse-hub|muse|merge"; then
    AGENT_ROLE="muse-specialist"
  elif echo "$ISSUE_LABELS" | grep -qE "db-schema|alembic|migration"; then
    AGENT_ROLE="database-architect"
  fi
  printf "WORKFLOW=issue-to-pr\nISSUE_NUMBER=%s\nISSUE_TITLE=%s\nISSUE_URL=https://github.com/%s/issues/%s\nPHASE_LABEL=%s\nROLE=%s\n" \
    "$NUM" "$TITLE" "$GH_REPO" "$NUM" "$PHASE_LABEL" "$AGENT_ROLE" > "$WT/.agent-task"
  echo "✅ worktree issue-$NUM ready"
done

git worktree list
```

After running this, open one Cursor composer window per worktree, each rooted
in its `issue-<N>` directory, and paste the Kickoff Prompt below.

---

## Environment (agents read this first)

**You are running inside a Cursor worktree.** Your working directory is NOT the main repo.

```bash
# Derive paths — run these at the start of your session
REPO=$(git worktree list | head -1 | awk '{print $1}')   # local filesystem path to main repo
WTNAME=$(basename "$(pwd)")                               # this worktree's name
# Docker path to your worktree: /worktrees/$WTNAME

# GitHub repo slug — HARDCODED. NEVER derive from local path or directory name.
# The local path is /Users/gabriel/dev/tellurstori/maestro — "tellurstori" is NOT the GitHub org.
export GH_REPO=cgcardona/maestro
```

| Item | Value |
|------|-------|
| Your worktree root | current directory (contains `.agent-task`) |
| Main repo (local path) | first entry of `git worktree list` |
| GitHub repo slug | `cgcardona/maestro` — always hardcoded, never derived |
| Docker compose location | main repo |
| Your worktree inside Docker | `/worktrees/$WTNAME` |

**All `docker compose exec` commands must be run from the main repo:**
```bash
cd "$REPO" && docker compose exec maestro <cmd>
```

### Docker sees your worktree directly — no file copying needed

`docker-compose.override.yml` bind-mounts the entire worktrees directory into
the container. After creating your feature branch, your worktree's code is
**immediately live inside the container at `/worktrees/$WTNAME/`**:

```bash
REPO=$(git worktree list | head -1 | awk '{print $1}')
WTNAME=$(basename "$(pwd)")

# mypy
cd "$REPO" && docker compose exec maestro sh -c "PYTHONPATH=/worktrees/$WTNAME mypy /worktrees/$WTNAME/maestro/ /worktrees/$WTNAME/tests/"

# pytest (specific file)
cd "$REPO" && docker compose exec maestro sh -c "PYTHONPATH=/worktrees/$WTNAME pytest /worktrees/$WTNAME/tests/path/to/test_file.py -v"
```

**⚠️ NEVER copy files into the main repo** for testing purposes. That pollutes
the `dev` branch with uncommitted changes.

> **Alembic exception:** `alembic revision --autogenerate` must run from the main repo
> because it needs a live DB connection. After generating, immediately `git mv` the
> migration file into your worktree and delete the copy from the main repo.

### Command policy

Consult `.cursor/AGENT_COMMAND_POLICY.md` for the full tier list. Summary:
- **Green (auto-allow):** `ls`, `git status/log/diff/fetch`, `gh pr view`, `mypy`, `pytest`, `rg`
- **Yellow (review before running):** `docker compose build`, `rm <single file>`, `git rebase`
- **Red (never):** `rm -rf`, `git push --force`, `git push origin dev`, `docker system prune`

---

## Kickoff Prompt

```
PARALLEL AGENT COORDINATION — ISSUE TO PR

Read .cursor/AGENT_COMMAND_POLICY.md before issuing any shell commands.
Green-tier commands run without confirmation. Yellow = check scope first.
Red = never, ask the user instead.

STEP 0 — READ YOUR TASK:
  cat .agent-task
  This file tells you your issue number, title, and URL. Substitute your actual
  issue number wherever you see <N> below.

STEP 0.5 — LOAD YOUR ROLE:
  ROLE=$(grep '^ROLE=' .agent-task | cut -d= -f2)
  REPO=$(git worktree list | head -1 | awk '{print $1}')
  ROLE_FILE="$REPO/.cursor/roles/${ROLE}.md"
  if [ -f "$ROLE_FILE" ]; then
    cat "$ROLE_FILE"
    echo "✅ Operating as role: $ROLE"
  else
    echo "⚠️  No role file found for '$ROLE' — proceeding without role context."
  fi
  # The decision hierarchy, quality bar, and failure modes in that file govern
  # all your choices from this point forward.

STEP 1 — DERIVE PATHS:
  REPO=$(git worktree list | head -1 | awk '{print $1}')   # local filesystem path only
  WTNAME=$(basename "$(pwd)")
  # Your worktree is live in Docker at /worktrees/$WTNAME — NO file copying needed.
  # All docker compose commands: cd "$REPO" && docker compose exec maestro <cmd>

  # GitHub repo slug — HARDCODED. NEVER derive from directory name, basename, or local path.
  # The local path is /Users/gabriel/dev/tellurstori/maestro.
  # "tellurstori" is the LOCAL directory — it is NOT the GitHub org.
  # The GitHub org is "cgcardona". Using the wrong slug → "Forbidden" or "Repository not found".
  export GH_REPO=cgcardona/maestro

  # ⚠️  VALIDATION — run this immediately to catch slug errors early:
  gh repo view "$GH_REPO" --json name --jq '.name'
  # Expected output: maestro
  # If you see an error → GH_REPO is wrong. Stop and fix it before continuing.

  # All gh commands inherit $GH_REPO automatically. You may also pass --repo "$GH_REPO" explicitly.

STEP 2 — CHECK CANONICAL STATE BEFORE DOING ANY WORK:
  ⚠️  Query GitHub first. Do NOT create a branch, write a file, or run mypy until
  you have confirmed no prior work exists. This is the idempotency gate.

  # 0. Is the issue itself already closed? (fastest exit — check this FIRST)
  ISSUE_STATE=$(gh issue view <N> --json state --jq '.state')
  if [ "$ISSUE_STATE" = "CLOSED" ]; then
    echo "⚠️  Issue #<N> is already CLOSED on GitHub. No work needed."
    # Self-destruct and stop.
    WORKTREE=$(pwd)
    cd "$REPO"
    git worktree remove --force "$WORKTREE"
    git worktree prune
    exit 0
  fi

  # 1. Is there already an open or merged PR that closes this issue?
  gh pr list --search "closes #<N>" --state all --json number,url,state,headRefName

  # 2. Is there already a branch for this issue in the remote?
  git ls-remote origin | grep -i "issue-<N>\|fix/.*<N>\|feat/.*<N>"

  Decision matrix — act on the FIRST match:
  ┌──────────────────────────────────────────────────────────────────────┐
  │ Issue is CLOSED       → STOP. Report closed state. Self-destruct.   │
  │ Merged PR found       → STOP. Report the PR URL. Self-destruct.     │
  │ Open PR found         → STOP. Report the PR URL. Self-destruct.     │
  │ Remote branch exists, │                                              │
  │   no PR yet           → Checkout that branch, skip to STEP 4.      │
  │ Nothing found         → Continue to STEP 3 (full implementation).   │
  └──────────────────────────────────────────────────────────────────────┘

  Self-destruct when stopping early:
    WORKTREE=$(pwd)
    cd "$REPO"
    git worktree remove --force "$WORKTREE"
    git worktree prune

STEP 3 — IMPLEMENT (only if STEP 2 found nothing):
  Read and follow every step in .github/CREATE_PR_PROMPT.md exactly.
  Steps: issue analysis → branch (from dev) → implement → mypy → tests → commit → docs → PR.

  # Create your feature branch from current HEAD (already at dev tip)
  git checkout -b feat/<short-description>

  mypy (run BEFORE tests — fix all type errors first):
    cd "$REPO" && docker compose exec maestro sh -c "PYTHONPATH=/worktrees/$WTNAME mypy /worktrees/$WTNAME/maestro/ /worktrees/$WTNAME/tests/"

  ⚠️  TYPE-SYSTEM RULES — mypy must be fixed correctly, not worked around:
    - No cast() at call sites — fix the callee's return type, not the caller.
    - No Any. Use TypeAlias, TypeVar, Protocol, Union, or typed wrappers at 3rd-party edges.
    - No `object` as a type annotation — be specific.
    - No naked collections at boundaries: dict[str, Any], list[dict], bare tuples = code smell.
      Wrap in a named entity. Convention: <Domain><Concept>Result (DynamicsResult, SwingAnalysis).
    - No # type: ignore without an inline comment citing the specific 3rd-party issue.
    - No non-ASCII characters inside b"..." bytes literals — mypy rejects them with
      "Bytes can only contain ASCII literal characters". Use only plain ASCII in byte
      strings; encode Unicode values explicitly (e.g. "MIDI v2 \u2014 newer".encode()).
    - Two failed fix attempts = stop and redesign — never loop with incremental tweaks.
    - Every public function signature is a contract. Register new result types in docs/reference/type_contracts.md.

  pytest — TARGETED TESTS ONLY (never the full suite):
  The full suite takes several minutes and is CI's job, not an agent's job.
  Derive test targets from what you changed using module-name convention:

    maestro/core/pipeline.py          → tests/test_pipeline.py
    maestro/core/intent*.py           → tests/test_intent*.py
    maestro/core/maestro_handlers.py  → tests/test_maestro_handlers.py
    maestro/services/muse_*.py        → tests/test_muse_*.py
    maestro/api/routes/muse.py        → tests/test_muse.py
    maestro/mcp/                      → tests/test_mcp.py
    maestro/daw/                      → tests/test_daw_adapter.py
    storpheus/music_service.py        → storpheus/test_gm_resolution.py + storpheus/test_*.py

  Run only the derived targets:
    cd "$REPO" && docker compose exec maestro sh -c \
      "PYTHONPATH=/worktrees/$WTNAME pytest \
       /worktrees/$WTNAME/tests/test_<module1>.py \
       /worktrees/$WTNAME/tests/test_<module2>.py \
       -v"

  If you added a new module with no existing test file, create tests/test_<module>.py
  and run that. Never fall back to tests/ as a directory.

  DOCS — non-negotiable, same commit as code:
    - Docstrings on every new module, class, and public function (why + contract, not what)
    - For new `muse <cmd>`: add a section to docs/architecture/muse_vcs.md with:
        purpose, flags table, output example, result type, agent use case
    - Register new named result types in docs/reference/type_contracts.md
    - Docs are written for AI agent consumers — explain the contract and when to call this

  After tests pass — cascading failure scan:
    Search for similar assertions or fixtures across other test files before declaring complete.
    A fix that changes a constant, model field, or shared contract likely affects more than one
    test file. Find and fix all of them in the same commit.

  Broken tests from other agents — fix them anyway:
    If you encounter a failing test that your implementation did NOT introduce,
    fix it before opening your PR. Include the fix in your branch with message:
    "fix: repair broken test <name> (pre-existing failure from dev)"
    Note it in your PR description. Never leave a broken test for the next agent.

STEP 4 — PRE-PUSH SYNC (critical — always run before pushing):
  ⚠️  Other agents may have merged PRs while you were implementing. Sync with dev
  NOW to catch conflicts locally rather than at merge time.

  ⚠️  COMMIT GUARD — run this first, every time, no exceptions:
  Git will abort the merge if any locally modified file is also changed on origin/dev.
  An uncommitted working tree WILL abort. This guard prevents that.

  git add -A
  git diff --cached --quiet || git commit -m "chore: commit remaining changes before dev sync"

  # Pre-check: these three files conflict on virtually every parallel Muse batch.
  # Know the rules before you merge so you can resolve mechanically, not by guessing.
  #
  #   FILE                              ALWAYS-SAFE RULE
  #   maestro/muse_cli/app.py           Keep ALL app.add_typer() lines from both sides.
  #   docs/architecture/muse_vcs.md    Keep ALL ## sections from both sides, sort alpha.
  #   docs/reference/type_contracts.md Keep ALL entries from both sides.

  git fetch origin
  git merge origin/dev

  ── CONFLICT PLAYBOOK (reference this immediately when git reports conflicts) ──
  │                                                                              │
  │ STEP A — See what conflicted (one command):                                 │
  │   git status | grep "^UU"                                                   │
  │                                                                              │
  │ STEP A.5 — UNIVERSAL TRIAGE (run for EVERY conflict before step B):        │
  │                                                                              │
  │   Peek at the conflict shape for each file:                                 │
  │     git diff --diff-filter=U -- <file> | grep -A6 "^<<<<<<<"               │
  │                                                                              │
  │   Apply the FIRST matching rule — stop as soon as one matches:             │
  │                                                                              │
  │   RULE 0 ─ ONE SIDE EMPTY (most common in parallel batches):               │
  │   ┌──────────────────────────────────────────────────────────────────────┐  │
  │   │  <<<<<<< HEAD                                                        │  │
  │   │  (blank / whitespace only)        ← this side is empty              │  │
  │   │  =======                                                             │  │
  │   │  <real content>                   ← this side has content           │  │
  │   │  >>>>>>> origin/dev                                                  │  │
  │   │  — OR the reverse (HEAD has content, origin/dev is blank/stub).     │  │
  │   │                                                                      │  │
  │   │  Action: TAKE the non-empty side. Remove markers. Done.             │  │
  │   │  This is always safe. The empty side is a base-file placeholder,   │  │
  │   │  NOT intentionally deleted content. No further analysis needed.     │  │
  │   │  Do NOT open the file to "verify" — just take the non-empty side.  │  │
  │   └──────────────────────────────────────────────────────────────────────┘  │
  │                                                                              │
  │   RULE 1 ─ BOTH SIDES IDENTICAL:                                            │
  │     Keep either side, remove markers. Done.                                │
  │                                                                              │
  │   RULE 2 ─ KNOWN ADDITIVE FILE → apply the file-specific rule in STEP B:  │
  │     muse_cli/app.py  •  muse_vcs.md  •  type_contracts.md                 │
  │                                                                              │
  │   RULE 3 ─ ALL OTHER FILES (judgment conflict):                             │
  │     Preserve dev's version PLUS your additions.                            │
  │     Semantically incompatible → STOP and report to user. Never guess.     │
  │                                                                              │
  │ STEP B — For each conflicted file NOT resolved by STEP A.5 (Rules 0–1):   │
  │                                                                              │
  │ ┌─ maestro/muse_cli/app.py ─────────────────────────────────────────────┐  │
  │ │ Each parallel agent adds exactly one app.add_typer() line.            │  │
  │ │ Pattern:                                                               │  │
  │ │   <<<<<<< HEAD                                                         │  │
  │ │   app.add_typer(foo_app, name="foo", ...)                              │  │
  │ │   =======                                                              │  │
  │ │   app.add_typer(bar_app, name="bar", ...)                              │  │
  │ │   >>>>>>> origin/dev                                                   │  │
  │ │ Rule: KEEP BOTH LINES. Remove markers. Never drop a line.             │  │
  │ │ Verify: grep -c "add_typer" maestro/muse_cli/app.py                   │  │
  │ │   count must equal the total number of registered sub-apps            │  │
  │ └───────────────────────────────────────────────────────────────────────┘  │
  │                                                                              │
  │ ┌─ docs/architecture/muse_vcs.md ───────────────────────────────────────┐  │
  │ │ Count markers first:                                                   │  │
  │ │   grep -c "^<<<<<" docs/architecture/muse_vcs.md                      │  │
  │ │ That is how many conflict blocks you must resolve.                     │  │
  │ │                                                                        │  │
  │ │ Pattern A — both sides have a real ## section:                        │  │
  │ │   Rule: KEEP BOTH sections, sorted alphabetically by command name.    │  │
  │ │                                                                        │  │
  │ │ Pattern B — one side is empty or a blank stub:                        │  │
  │ │   Rule: KEEP the non-empty side entirely. Discard the empty side.    │  │
  │ │                                                                        │  │
  │ │ Pattern C — both sides edited the SAME section differently:           │  │
  │ │   Rule: keep the more complete / accurate version.                    │  │
  │ │                                                                        │  │
  │ │ Final check (must return empty):                                       │  │
  │ │   grep -n "<<<<<<\|=======\|>>>>>>>" docs/architecture/muse_vcs.md   │  │
  │ └───────────────────────────────────────────────────────────────────────┘  │
  │                                                                              │
  │ ┌─ docs/reference/type_contracts.md ────────────────────────────────────┐  │
  │ │ Rule: KEEP ALL entries from BOTH sides. Remove markers.              │  │
  │ │ Final check: grep -n "<<<<<<\|=======\|>>>>>>>" docs/reference/type_contracts.md │
  │ └───────────────────────────────────────────────────────────────────────┘  │
  │                                                                              │
  │ ┌─ Any other file (JUDGMENT CONFLICTS) ─────────────────────────────────┐  │
  │ │ • Preserve dev's version PLUS your additions.                        │  │
  │ │ • If dev already contains your feature → stop and self-destruct.     │  │
  │ │ • If semantically incompatible → stop, report to user.              │  │
  │ └───────────────────────────────────────────────────────────────────────┘  │
  │                                                                              │
  │ STEP C — After resolving ALL files:                                         │
  │   git add <resolved-files>                                                  │
  │   git commit -m "chore: resolve merge conflicts with origin/dev"            │
  │                                                                              │
  │ STEP D — Verify clean (no markers anywhere):                                │
  │   git diff --check    ← must return nothing                                 │
  │                                                                              │
  │ STEP E — Re-run mypy only if Python files were in conflict:                 │
  │   app.py changed → run mypy. Markdown-only conflicts → skip mypy.          │
  │   Re-run targeted tests only if logic files changed.                        │
  │                                                                              │
  │ STEP F — Advanced diagnostics if needed:                                    │
  │   git log --oneline origin/dev...HEAD  ← commits this branch adds          │
  │   git diff origin/dev...HEAD           ← full delta vs dev                 │
  │   git show origin/dev:path/to/file     ← see dev's version of a file       │
  └──────────────────────────────────────────────────────────────────────────────

STEP 5 — PUSH & CREATE PR:
  git push origin feat/<short-description>

  gh pr create \
    --base dev \
    --head feat/<short-description> \
    --title "feat: <issue title>" \
    --body "$(cat <<'EOF'
  ## Summary
  Closes #<N> — <one-line description>.

  ## Root Cause / Motivation
  <What was wrong or missing and why>

  ## Solution
  <What was changed and why this approach>

  ## Verification
  - [ ] mypy clean
  - [ ] Tests pass
  - [ ] Docs updated
  EOF
  )"

  ⚠️  VERIFY AUTO-CLOSE LINKAGE — run immediately after gh pr create:
  # GitHub auto-closes issue #<N> when the PR is merged ONLY if "Closes #<N>"
  # appears verbatim in the PR body. Verify now so you don't leave a ghost issue.

  PR_BODY=$(gh pr list \
    --repo cgcardona/maestro \
    --head feat/<short-description> \
    --json body \
    --jq '.[0].body')

  echo "$PR_BODY" | grep -i "closes #<N>"
  # Expected output: a line containing "Closes #<N>"
  # If grep returns nothing → the PR body is missing the close keyword.
  # Fix immediately:
  #   gh pr edit feat/<short-description> --body "$(echo "$PR_BODY")
  #
  # Closes #<N>"

STEP 6 — SELF-DESTRUCT (always run this after the PR is open or after an early stop):
  WORKTREE=$(pwd)
  cd "$REPO"
  git worktree remove --force "$WORKTREE"
  git worktree prune

⚠️  NEVER copy files to the main repo for testing.
⚠️  NEVER start implementation without completing STEP 2. Skipping the check
    causes duplicate branches, duplicate PRs, and wasted cycles.
⚠️  NEVER push without running STEP 4 (pre-push sync). This is the primary
    defence against merge conflicts and regressions on dev.

Report: issue number, PR URL (existing or newly created), fix summary, tests added,
any protocol changes requiring handoff.
⚠️  A PR URL is required — "Done" without an artifact URL is not an acceptable report.
```

---

## Before launching

### Step A — Label audit (do this first, every time)

The GitHub label is the **single source of truth** for what belongs in a phase.
Run this before touching the Setup script:

```bash
# List every open issue for the current phase — this IS your candidate pool.
gh issue list \
  --repo cgcardona/maestro \
  --label "phase-1" \
  --state open \
  --json number,title,url \
  --jq '.[] | "#\(.number)  \(.title)\n  \(.url)"'
```

- If the list is **empty** → phase is complete. Do not launch.
- If the list has issues **not yet assigned to a batch** → they must be included
  in the current or next batch before you can call the phase done.
- If an issue has an **open PR** already → the agent will find it in STEP 2 and
  self-destruct. Still include it so the batch reflects reality.

### Step B — Select your batch (up to 4 issues)

From the label audit output, choose issues that satisfy **both** criteria in
**Issue selection** above (foundational first + zero file overlap). Read each
candidate's body to identify affected files:

```bash
gh issue view <N> --repo cgcardona/maestro --json body,title
```

Confirm no two selected issues share a file. Document your selection in the
`SELECTED_ISSUES` array inside the Setup script.

### Step B.5 — File overlap pre-check (run before creating worktrees)

After selecting candidates, verify none of them share files with each other
**or** with currently open PRs. Any overlap = serialize into the next batch.

```bash
REPO=$(git rev-parse --show-toplevel)
cd "$REPO"

echo "=== Files touched by currently open PRs ==="
for num in $(gh pr list --state open --json number --jq '.[].number'); do
  files=$(gh pr diff "$num" --name-only 2>/dev/null)
  if [ -n "$files" ]; then
    title=$(gh pr view "$num" --json title --jq .title 2>/dev/null)
    echo ""
    echo "PR #$num — $title:"
    echo "$files" | sed 's/^/  /'
  fi
done

echo ""
echo "⚠️  Any file appearing in TWO entries above = conflict at merge time."
echo "⚠️  Resolve: finish the earlier PR first, then rebase the later issue off dev."
```

**Sequential batching rule:** Only launch issues with zero file overlap across
all open PRs AND across each other. A two-batch structure (`batch-1 → merge
all → batch-2`) eliminates most conflicts. Never mix dependent issues into the
same parallel batch.

**Dependency detection:** If issue B cannot function without A's code:
1. Note `**Depends on #A**` in B's issue body.
2. Label B as `blocked`.
3. Merge A first, then un-block B and add it to the next batch.

### Step C — Confirm `dev` is up to date

```bash
REPO=$(git rev-parse --show-toplevel)
git -C "$REPO" fetch origin
git -C "$REPO" merge origin/dev
```

> **Why `fetch + merge` and not `git pull`?** `git pull --rebase` fails when there are
> uncommitted changes in the main worktree. `git pull` (merge mode) can also be blocked by
> sandbox restrictions that prevent git from writing to `.git/config`. `fetch + merge` is
> always safe and never needs sandbox elevation.

### Step D — Run the Setup script, then verify

After running the Setup script:

```bash
git worktree list   # one entry per selected issue + main repo
```

### Step E — Confirm Docker is running and worktrees are mounted

```bash
REPO=$(git rev-parse --show-toplevel)
docker compose -f "$REPO/docker-compose.yml" ps
docker compose exec maestro ls /worktrees/
```

---

## After agents complete

### 1 — Closure audit (required before declaring a phase done)

Run this after all PRs in the batch are **merged** (not just opened):

```bash
PHASE_LABEL="phase-1"   # match the label used in Setup

REMAINING=$(gh issue list \
  --repo cgcardona/maestro \
  --label "$PHASE_LABEL" \
  --state open \
  --json number \
  --jq 'length')

if [ "$REMAINING" -gt 0 ]; then
  echo "⚠️  $REMAINING open issue(s) still labeled '$PHASE_LABEL':"
  gh issue list \
    --repo cgcardona/maestro \
    --label "$PHASE_LABEL" \
    --state open \
    --json number,title,url \
    --jq '.[] | "#\(.number)  \(.title)\n  \(.url)"'
  echo ""
  echo "→ These need implementation, review, or explicit closure before moving to the next phase."
  echo "→ Common causes:"
  echo "   • Issue was labeled phase-X but not included in any batch (add to next batch)"
  echo "   • PR was merged without 'Closes #N' in the body (close the issue manually)"
  echo "   • Issue describes work that was done in a different PR (close with a comment citing that PR)"
else
  echo "✅ All '$PHASE_LABEL' issues are closed. Phase is complete — safe to advance."
fi
```

**Do not advance to the next phase until this script prints the ✅ line.**

### 2 — PR audit

```bash
gh pr list --repo cgcardona/maestro --state open
```

All PRs from this batch should be open (awaiting review) or merged. None should be closed/rejected without a corresponding issue closure.

### 3 — Worktree cleanup

```bash
git worktree list   # should show only the main repo
# If stale worktrees linger (agent crashed before self-destructing):
git -C "$(git rev-parse --show-toplevel)" worktree prune
```

### 4 — Main repo cleanliness ⚠️ run this every batch, no exceptions

An agent that violates the "never copy files into the main repo" rule leaves
uncommitted changes in the main working tree. These are silent — git status
won't warn you unless you look. Left unchecked they accumulate across batches,
creating phantom diffs that are impossible to attribute.

```bash
REPO=$(git rev-parse --show-toplevel)
git -C "$REPO" status
# Must show: nothing to commit, working tree clean
```

**If dirty files are found:**

1. Check whether the work is already merged:
   ```bash
   # For each dirty file, find the PR that contains it:
   gh pr list --state merged --json number,title --jq '.[].number' | \
     xargs -I{} gh pr diff {} --name-only 2>/dev/null | grep <filename>
   ```
2. **If already merged** → the dirty files are stale copies. Discard them:
   ```bash
   git -C "$REPO" restore --staged .
   git -C "$REPO" restore .
   rm -f <any .bak or untracked agent artifacts>
   ```
3. **If NOT merged** → the agent likely wrote directly to the main repo instead
   of staying in its worktree. Create a branch, commit the work, and open a PR:
   ```bash
   git -C "$REPO" checkout -b fix/<description>
   git -C "$REPO" add -A
   git -C "$REPO" commit -m "feat: <description> (rescued from main repo dirty state)"
   git push origin fix/<description>
   gh pr create --base dev --head fix/<description> ...
   ```

### 5 — Hand off to PR review

PRs from this batch are immediately available for the **PARALLEL_PR_REVIEW.md** workflow. Run that now — issues only close automatically when PRs are **merged**, not just opened.

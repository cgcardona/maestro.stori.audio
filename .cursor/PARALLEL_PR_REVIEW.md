# Parallel Agent Kickoff — PR Review → Grade → Merge

> ## YOU ARE THE COORDINATOR
>
> If you are an AI agent reading this document, your role is **coordinator only**.
>
> **Your job — the full list, nothing more:**
> 1. Pull `dev` to confirm it is up to date.
> 2. Run the Setup script below to create one worktree per PR and write a `.agent-task` file into each.
> 3. Launch one sub-agent per worktree using the **Task tool** (preferred — no limit on simultaneous agents) or a Cursor composer window rooted in that worktree.
> 4. Report back once all sub-agents have been launched.
>
> **You do NOT:**
> - Check out branches or review any PR yourself.
> - Run mypy or pytest yourself.
> - Merge or close any PR yourself.
> - Read PR diffs or study code yourself.
>
> The **Kickoff Prompt** at the bottom of this document is for the sub-agents, not for you.
> Do not follow it yourself.

---

## Why `.agent-task` files unlock more than 4 parallel agents

The Task tool can launch multiple review agents simultaneously from a single
coordinator message. Each agent reads its own `.agent-task` file, which carries
the PR number, branch, expected grade threshold, and file-overlap context.
The coordinator needs only the worktree path and the kickoff prompt — no
per-PR content to embed in the call:

```python
# All launched simultaneously — no 4-agent limit
Task(worktree="/path/to/pr-315", prompt=KICKOFF_PROMPT)
Task(worktree="/path/to/pr-316", prompt=KICKOFF_PROMPT)
Task(worktree="/path/to/pr-317", prompt=KICKOFF_PROMPT)
Task(worktree="/path/to/pr-318", prompt=KICKOFF_PROMPT)
Task(worktree="/path/to/pr-319", prompt=KICKOFF_PROMPT)
```

**Nested orchestration:** A review agent whose `.agent-task` contains
`SPAWN_SUB_AGENTS=true` acts as a sub-coordinator — useful when a large PR
needs multiple independent reviewers (e.g. one for types, one for tests, one
for docs). Each sub-reviewer writes its grade and findings into its own
worktree; the sub-coordinator collects them and emits a composite grade.

See `PARALLEL_BUGS_TO_ISSUES.md` → "Agent Task File Reference" for the
full field reference.

---

Each sub-agent gets its own ephemeral worktree. Worktrees are created at kickoff,
named by PR number, and **deleted by the sub-agent when its job is done** — whether
the PR was merged, rejected, or left for human review. The branch lives on
GitHub regardless; the local worktree is just a working directory.

---

## Architecture

```
Kickoff (coordinator)
  └─ for each PR:
       DEV_SHA=$(git rev-parse dev)
       git worktree add --detach .../pr-<N> "$DEV_SHA"  ← detached HEAD at dev tip
       write .agent-task into it                         ← task assignment, no guessing
       launch agent in that directory

Agent (per worktree)
  └─ cat .agent-task                        ← knows exactly what to do
  └─ gh pr view <N> --json state,...        ← CHECK FIRST: merged/closed/approved?
                                               if so → stop + self-destruct
  └─ gh pr checkout <N>                     ← checks out the PR branch (only if open)
  └─ git fetch origin && git merge origin/dev  ← sync latest dev into feature branch
  └─ review → grade
  └─ git fetch origin && git merge origin/dev  ← final sync before merge
  └─ git push origin "$BRANCH"             ← push resolution so GitHub sees clean state
  └─ sleep 5 && gh pr merge <N> --squash  ← merge only after remote is up to date
  └─ git push origin --delete "$BRANCH"   ← remote branch cleanup
  └─ git -C <main-repo> branch -D "$BRANCH"  ← local branch cleanup
  └─ git worktree remove --force <path>     ← self-destructs when done
  └─ git -C <main-repo> worktree prune      ← cleans up the ref
```

Worktrees are **not** kept around between cycles. If an agent crashes before
cleanup, run `git worktree prune` from the main repo.

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

# Enable rerere so git caches conflict resolutions across agents.
# When multiple agents resolve the same conflict (e.g. muse_vcs.md), rerere
# automatically reuses the recorded resolution — no manual work needed.
# || true: the sandbox blocks .git/config writes (EPERM) when this runs as
# part of a multi-statement block. rerere is an optimization, not critical.
git config rerere.enabled true || true

# Snapshot dev tip — all worktrees start here; agents checkout their PR branch in STEP 3
DEV_SHA=$(git rev-parse dev)

# ── DEFINE PRs ───────────────────────────────────────────────────────────────
# Format: "PR_NUMBER|PR_TITLE"
# Update this list for each review batch.
declare -a PRS=(
  "315|feat(musehub): unread notification badge in nav header"
  "316|feat(musehub): session detail — participant avatars, profile links, commit cards"
  "317|feat(musehub): explore and trending — richer repo cards with BPM, key, tags"
  "318|feat(musehub): insights dashboard — view count, download count, traffic sparkline"
)

# ── CREATE WORKTREES + AGENT TASK FILES ──────────────────────────────────────
for entry in "${PRS[@]}"; do
  NUM="${entry%%|*}"
  TITLE="${entry##*|}"
  WT="$PRTREES/pr-$NUM"
  if [ -d "$WT" ]; then
    echo "⚠️  worktree pr-$NUM already exists, skipping"
    continue
  fi
  git worktree add --detach "$WT" "$DEV_SHA"
  # Assign ROLE based on PR labels:
  #   muse, muse-cli, muse-hub, merge labels → muse-specialist (with pr-reviewer discipline)
  #   all others → pr-reviewer
  PR_LABELS=$(gh pr view "$NUM" --repo "$GH_REPO" --json labels --jq '[.labels[].name] | join(",")' 2>/dev/null || echo "")
  REVIEW_ROLE="pr-reviewer"
  if echo "$PR_LABELS" | grep -qE "muse-cli|muse-hub|muse|merge"; then
    REVIEW_ROLE="muse-specialist"
  fi

  # Fetch PR metadata for the task file
  PR_BRANCH=$(gh pr view "$NUM" --repo "$GH_REPO" --json headRefName --jq '.headRefName' 2>/dev/null || echo "unknown")
  PR_FILES=$(gh pr diff "$NUM" --repo "$GH_REPO" --name-only 2>/dev/null | tr '\n' ',' | sed 's/,$//')
  PR_BODY=$(gh pr view "$NUM" --repo "$GH_REPO" --json body --jq '.body' 2>/dev/null || echo "")
  CLOSES_ISSUE=$(echo "$PR_BODY" | grep -oE '[Cc]loses?\s+#[0-9]+' | grep -oE '[0-9]+' | tr '\n' ',' | sed 's/,$//')
  # MERGE_AFTER: PR number that must be merged before this one (for Alembic chain safety).
  # Set automatically if the PR body contains "Merges after #NNN" or "Depends on PR #NNN".
  # The coordinator can also set this manually for known sequential batches.
  MERGE_AFTER_VAL=$(echo "$PR_BODY" | grep -oiE 'merge after #[0-9]+|depends on pr #[0-9]+' | grep -oE '[0-9]+' | head -1)
  [ -z "$MERGE_AFTER_VAL" ] && MERGE_AFTER_VAL="${MERGE_AFTER:-none}"
  # HAS_MIGRATION: auto-detect. If true, reviewer must run Alembic chain validation.
  HAS_MIGRATION=$(echo "$PR_FILES" | grep -c "alembic/versions/" || echo 0)
  [ "$HAS_MIGRATION" -gt 0 ] && HAS_MIGRATION_VAL=true || HAS_MIGRATION_VAL=false

  cat > "$WT/.agent-task" << TASKEOF
WORKFLOW=pr-review
GH_REPO=$GH_REPO
PR_NUMBER=$NUM
PR_TITLE=$TITLE
PR_URL=https://github.com/$GH_REPO/pull/$NUM
PR_BRANCH=$PR_BRANCH
CLOSES_ISSUES=$CLOSES_ISSUE
FILES_CHANGED=$PR_FILES
MERGE_AFTER=$MERGE_AFTER_VAL
HAS_MIGRATION=$HAS_MIGRATION_VAL
ROLE=$REVIEW_ROLE
SPAWN_SUB_AGENTS=false
ATTEMPT_N=0
REQUIRED_OUTPUT=grade,merge_status,pr_url
ON_BLOCK=stop
TASKEOF

  echo "✅ worktree pr-$NUM ready (role: $REVIEW_ROLE)"
done

git worktree list
```

After running this, launch one agent per worktree using the **Task tool**
(preferred — no limit on simultaneous agents) or a Cursor composer window
rooted in each `pr-<N>` directory.

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
the container. After checking out the PR branch, your worktree's code is
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
the `dev` branch with uncommitted changes that don't belong there.

> **Alembic exception:** If the PR includes a migration, verify migration correctness by
> reading the file rather than applying it during review.

### Command policy

Consult `.cursor/AGENT_COMMAND_POLICY.md` for the full tier list. Summary:
- **Green (auto-allow):** `ls`, `git status/log/diff/fetch`, `gh pr view`, `mypy`, `pytest`, `rg`
- **Yellow (review before running):** `docker compose build`, `rm <single file>`, `git rebase`
- **Red (never):** `rm -rf`, `git push --force`, `git push origin dev`, `docker system prune`

---

## Kickoff Prompt

```
PARALLEL AGENT COORDINATION — PR REVIEW

Read .cursor/AGENT_COMMAND_POLICY.md before issuing any shell commands.
Green-tier commands run without confirmation. Yellow = check scope first.
Red = never, ask the user instead.

STEP 0 — READ YOUR TASK FILE:
  cat .agent-task

  Parse all KEY=value fields from the header:
    GH_REPO          → GitHub repo slug (export immediately)
    PR_NUMBER        → your PR number (substitute for <N> throughout)
    PR_TITLE         → PR title
    PR_URL           → full GitHub URL for reference
    PR_BRANCH        → feature branch name (use instead of querying GitHub)
    CLOSES_ISSUES    → comma-separated issue numbers this PR closes (from PR body)
    FILES_CHANGED    → comma-separated list of files this PR touches
    MERGE_AFTER      → PR number that must be merged before this one ("none" = no gate)
    HAS_MIGRATION    → "true" if this PR includes Alembic migration files
    SPAWN_SUB_AGENTS → if true, act as sub-coordinator (create sub-reviewers
                       for types/tests/docs and emit a composite grade)

  Export for all subsequent commands:
    export GH_REPO=$(grep "^GH_REPO=" .agent-task | cut -d= -f2)
    export GH_REPO=${GH_REPO:-cgcardona/maestro}
    N=$(grep "^PR_NUMBER=" .agent-task | cut -d= -f2)
    BRANCH=$(grep "^PR_BRANCH=" .agent-task | cut -d= -f2)
    MERGE_AFTER=$(grep "^MERGE_AFTER=" .agent-task | cut -d= -f2)
    HAS_MIGRATION=$(grep "^HAS_MIGRATION=" .agent-task | cut -d= -f2)
    ATTEMPT_N=$(grep "^ATTEMPT_N=" .agent-task | cut -d= -f2)
    BATCH_ID=$(grep "^BATCH_ID=" .agent-task | cut -d= -f2)

  Generate your unique reviewer session ID (identifies THIS specific reviewer run):
    AGENT_SESSION="qa-$(date -u +%Y%m%dT%H%M%SZ)-$(printf '%04x' $RANDOM)"
    echo "🤖 Reviewer session: $AGENT_SESSION  Batch: ${BATCH_ID:-unset}"

  ⚠️  ANTI-LOOP GUARD: if ATTEMPT_N > 2 → STOP immediately.
    Self-destruct and escalate. Report the exact failure. Never loop blindly.

  ⚠️  RETRY-WITHOUT-STRATEGY-MUTATION: if a merge or fix attempt fails twice
    with the same error → change strategy. Two identical failures = wrong approach.

  Use FILES_CHANGED as your starting point for the review — check each file
  listed rather than running a full diff scan from scratch.

  ⚠️  If HAS_MIGRATION=true → you MUST run STEP 5.B (Alembic chain validation)
      before grading. A broken migration chain is an automatic C → mandatory fix.

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
  ⚠️  Query GitHub first. Do NOT checkout a branch, run mypy, or add a review
  comment until you have confirmed the PR is still open and unreviewed.
  This is the idempotency gate.

  # 1. What is the current state of this PR?
  gh pr view <N> --json state,mergedAt,reviews,reviewDecision,headRefName

  Decision matrix — act on the FIRST match:
  ┌────────────────────────────────────────────────────────────────────────┐
  │ state = "MERGED"   → STOP. Report already merged. Self-destruct.      │
  │ state = "CLOSED"   → STOP. Report already closed/rejected. Self-dest. │
  │ reviewDecision =   │                                                   │
  │   "APPROVED"       → STOP. Report already approved. Self-destruct.    │
  │ state = "OPEN",    │                                                   │
  │   no approval yet  → Continue to STEP 3 (full review).                │
  └────────────────────────────────────────────────────────────────────────┘

  Self-destruct when stopping early:
    WORKTREE=$(pwd)
    cd "$REPO"
    git worktree remove --force "$WORKTREE"
    git worktree prune

STEP 3 — CHECKOUT & SYNC (only if STEP 2 shows the PR is open and unreviewed):

  ⚠️  COMMIT GUARD — run this first if any files are modified in your worktree:
  Git will abort the merge if any tracked file has uncommitted local changes.
  Commit everything before touching the remote.

  git add -A
  git diff --cached --quiet || git commit -m "chore: stage worktree before dev sync"

  # 1. Checkout the PR branch in this worktree.
  #
  # ⚠️  NEVER use `gh pr checkout <N>` — it runs `git checkout` against the MAIN repo's
  #    working directory, not this worktree. This is what causes feat/* branches to appear
  #    checked out in the main repo. It is a known, recurring failure mode.
  #
  # ALWAYS use plain git inside this worktree directory:
  git fetch origin "$BRANCH"
  git checkout -b "$BRANCH" --track "origin/$BRANCH" 2>/dev/null || git checkout "$BRANCH"

  # 2. Fetch ALL remote refs (other agents may have merged PRs while you work)
  git fetch origin

  # 3. Pre-check: know what will conflict BEFORE you merge
  #    These three files conflict on virtually every parallel Muse batch.
  #    Read this section now so you can resolve mechanically when git stops.
  #
  #    FILE                              ALWAYS-SAFE RULE
  #    maestro/muse_cli/app.py           Keep ALL app.add_typer() lines from both sides.
  #    docs/architecture/muse_vcs.md    Keep ALL ## sections from both sides, sort alpha.
  #    docs/reference/type_contracts.md Keep ALL entries from both sides.
  #
  #    ⚡ SHORTCUT: open .cursor/CONFLICT_RULES.md — every common conflict in this
  #    repo has a one-line mechanical rule. Do NOT use sed/grep/hexdump loops.
  #    maestro/api/routes/musehub/__init__.py NEVER conflicts (auto-discovery).
  #    app.py, muse_vcs.md, type_contracts.md use union merge via .gitattributes.

  # 4. Merge the latest dev into this feature branch NOW
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
  │     Preserve dev's version PLUS this PR's additions.                       │
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
  │ │ For each block, identify pattern and apply rule:                       │  │
  │ │                                                                        │  │
  │ │ Pattern A — both sides have a real ## section:                        │  │
  │ │   <<<<<<< HEAD                                                         │  │
  │ │   ## muse foo — ...                                                    │  │
  │ │   =======                                                              │  │
  │ │   ## muse bar — ...                                                    │  │
  │ │   >>>>>>> origin/dev                                                   │  │
  │ │   Rule: KEEP BOTH, sorted alphabetically by command name.             │  │
  │ │                                                                        │  │
  │ │ Pattern B — one side is empty or a blank stub:                        │  │
  │ │   <<<<<<< HEAD                                                         │  │
  │ │   (blank or single-line stub)                                          │  │
  │ │   =======                                                              │  │
  │ │   ## muse bar — full content                                           │  │
  │ │   >>>>>>> origin/dev                                                   │  │
  │ │   Rule: KEEP the non-empty side entirely. Discard the empty side.    │  │
  │ │                                                                        │  │
  │ │ Pattern C — both sides edited the SAME section differently:           │  │
  │ │   Rule: read both, keep the more complete / accurate version.         │  │
  │ │   If genuinely unclear, keep both and note in the commit message.     │  │
  │ │                                                                        │  │
  │ │ Final check (must return empty):                                       │  │
  │ │   grep -n "<<<<<<\|=======\|>>>>>>>" docs/architecture/muse_vcs.md   │  │
  │ └───────────────────────────────────────────────────────────────────────┘  │
  │                                                                              │
  │ ┌─ docs/reference/type_contracts.md ────────────────────────────────────┐  │
  │ │ Each agent registers new named types. Both belong.                    │  │
  │ │ Rule: KEEP ALL entries from BOTH sides. Remove markers.              │  │
  │ │ Final check: grep -n "<<<<<<\|=======\|>>>>>>>" docs/reference/type_contracts.md │
  │ └───────────────────────────────────────────────────────────────────────┘  │
  │                                                                              │
  │ ┌─ Any other file (JUDGMENT CONFLICTS) ─────────────────────────────────┐  │
  │ │ These require reading both sides carefully.                            │  │
  │ │ • Preserve dev's version PLUS this PR's additions.                   │  │
  │ │ • If dev already contains this PR's feature → downgrade grade.       │  │
  │ │ • If semantically incompatible → stop, report to user.               │  │
  │ └───────────────────────────────────────────────────────────────────────┘  │
  │                                                                              │
  │ STEP C — After resolving ALL files:                                         │
  │   git add <resolved-files>                                                  │
  │   git commit -m "chore: resolve merge conflicts with origin/dev"            │
  │                                                                              │
  │ STEP D — Verify clean (no markers anywhere):                                │
  │   git diff --check                                                           │
  │   (should output nothing — any output means unresolvedmarkers remain)       │
  │                                                                              │
  │ STEP E — Re-run mypy only if resolved files contain Python changes:         │
  │   app.py changed → run mypy. Markdown-only conflicts → skip mypy.          │
  │   cd "$REPO" && docker compose exec maestro sh -c "PYTHONPATH=/worktrees/$WTNAME mypy /worktrees/$WTNAME/maestro/"        │
  │                                                                              │
  │ STEP F — Advanced diagnostics if needed:                                    │
  │   git log --oneline origin/dev...HEAD  ← commits this PR adds              │
  │   git diff origin/dev...HEAD           ← full delta vs dev                 │
  │   git show origin/dev:path/to/file     ← see dev's version of a file       │
  └──────────────────────────────────────────────────────────────────────────────

  ⚠️  If git merge reports "local changes would be overwritten":
    - Run git status to identify the unexpected modified files.
    - If they came from the checkout (gh pr checkout left dirty files), run:
        git checkout -- <file>   ← discard checkout-introduced changes, then retry merge
    - Then retry: git merge origin/dev

STEP 4 — TARGETED TEST SCOPING (before review):
  Identify which test files to run based on what this PR changes.
  NEVER run the full suite — that is CI's job, not an agent's job.

  # 1. What Python files does this PR change?
  CHANGED_PY=$(git diff origin/dev...HEAD --name-only | grep '\.py$')
  echo "$CHANGED_PY"

  # 2. What commits landed on dev since this branch diverged?
  git log --oneline HEAD..origin/dev

  # 3. Derive test targets using module-name convention:
  #    maestro/core/pipeline.py        → tests/test_pipeline.py
  #    maestro/services/muse_vcs.py    → tests/test_muse_vcs.py
  #    maestro/api/routes/muse.py      → tests/test_muse.py (or e2e/test_muse_e2e_harness.py)
  #    storpheus/music_service.py      → storpheus/test_music_service.py
  #    tests/test_*.py (already a test)→ run it directly
  #
  #    Quick reference (from .cursorrules):
  #      maestro/core/intent*.py           → tests/test_intent*.py
  #      maestro/core/pipeline.py          → tests/test_pipeline.py
  #      maestro/core/maestro_handlers.py  → tests/test_maestro_handlers.py
  #      maestro/services/muse_*.py        → tests/test_muse_*.py
  #      maestro/mcp/                      → tests/test_mcp.py
  #      maestro/daw/                      → tests/test_daw_adapter.py
  #      storpheus/music_service.py        → storpheus/test_gm_resolution.py + storpheus/test_*.py
  #
  # 4. If the PR only changes .cursor/, docs/, or other non-.py files: skip pytest entirely.
  #    mypy is irrelevant too. The review is markdown-content focused.

  # 5. Run only the derived targets (substitute real paths):
  cd "$REPO" && docker compose exec maestro sh -c \
    "PYTHONPATH=/worktrees/$WTNAME pytest \
     /worktrees/$WTNAME/tests/test_<module1>.py \
     /worktrees/$WTNAME/tests/test_<module2>.py \
     -v"

STEP 5 — REVIEW:
  Read and follow every step in .github/PR_REVIEW_PROMPT.md exactly.
  1. Context — read PR description, referenced issue, commits, files changed
  2. Deep review — work through all applicable checklist sections (3a–3j)

  TYPE SYSTEM — automatic C grade if any of these are present:
    - cast() at a call site (fix the callee)
    - Any in a return type, parameter, or TypedDict field
    - object as a type annotation
    - dict[str, Any], list[dict], or bare tuples crossing module boundaries
      (must be wrapped in a named entity: <Domain><Concept>Result)
    - # type: ignore without an inline comment naming the 3rd-party issue
    - See docs/reference/type_contracts.md for the canonical entity inventory

  DOCS — automatic C grade if any of these are missing:
    - Docstrings on every new public module, class, and function
    - New muse <cmd> section in docs/architecture/muse_vcs.md
      (must include: purpose, flags table, output example, result type, agent use case)
    - New result types registered in docs/reference/type_contracts.md
    - Docs in the same commit as code (not a follow-up PR)

  3. Add/fix tests if weak or missing

  ── STEP 5.A — BASELINE HEALTH SNAPSHOT (run BEFORE checking out the PR branch) ──
  Record the pre-existing state of dev so you know what errors are yours vs. already broken.
  This is your contract with the next agent — never skip it.

  # Checkout dev tip first, run full mypy + targeted tests, record results.
  git stash  # if you already have the PR branch checked out
  git checkout dev
  echo "=== PRE-EXISTING MYPY BASELINE (dev before PR) ==="
  cd "$REPO" && docker compose exec maestro sh -c \
    "PYTHONPATH=/worktrees/$WTNAME mypy /worktrees/$WTNAME/maestro/ /worktrees/$WTNAME/tests/" \
    2>&1 | tail -10
  # Note: any error shown here is pre-existing on dev — you own fixing it if it
  # is in a file this PR touches. Errors in untouched files → file a follow-up issue.

  echo "=== PRE-EXISTING TEST BASELINE (targeted) ==="
  # (Run targeted tests relevant to the PR's module — same files you'll test after merge)
  # Any failure here is pre-existing. Fix it before grading this PR.

  # Then check out the PR branch for review:
  git checkout "$PR_BRANCH" 2>/dev/null || git fetch origin && git checkout "$PR_BRANCH"

  ── STEP 5.B — MIGRATION CHAIN VALIDATION (skip if no migration files) ──────────
  # If the PR adds Alembic migration files, validate the revision chain before grading.
  # Two agents creating migrations in the same batch both named 0006_* is a chain break.
  #
  # 1. List all revision and down_revision lines:
  #    grep -r "^revision\|^down_revision" alembic/versions/
  # 2. Every down_revision must point to an existing revision ID.
  # 3. No two files may share the same revision ID.
  # 4. No two files may share the same down_revision (that creates a branch, not a chain).
  # 5. alembic heads must return exactly one head after merging this PR.
  #    cd "$REPO" && docker compose exec maestro alembic heads
  # If the chain is broken → MANDATORY fix before grading. Renumber the migration and
  # update its down_revision. This is a C-grade issue at minimum.

  4. Run mypy (FULL CODEBASE) then TARGETED tests (Docker-native):
     ⚠️  Run mypy across the ENTIRE codebase, not just the PR's files.
         This catches errors the PR may expose in sibling files.
     ⚠️  Tests: targeted files only — but cross-reference the baseline from STEP 5.A.
     ⚠️  Never pipe mypy/pytest through grep/head/tail — full output, exit code is authoritative.

  cd "$REPO" && docker compose exec maestro sh -c \
    "PYTHONPATH=/worktrees/$WTNAME mypy /worktrees/$WTNAME/maestro/ /worktrees/$WTNAME/tests/"

  5. Pre-existing failures — you own them if they are in files this PR touches:
     ─── mypy errors ───
     Any mypy error in a file this PR modifies that was ALSO present in the baseline
     (STEP 5.A) must be fixed in this review cycle. Commit the fix separately:
       "fix: resolve pre-existing mypy error in <file> — <brief description>"
     Errors in files this PR does NOT touch: file a GitHub issue, note it in report,
     do NOT block this merge on it.

     ─── broken tests ───
     Any test that failed in the baseline AND still fails after the PR is applied must
     be fixed before grading. Commit the fix separately:
       "fix: repair pre-existing broken test <name>"
     If the fix requires a major refactor (>30 min of work), add a pytest.mark.skip
     with a comment referencing a new GitHub issue. Never leave a silent red test.

  6. Red-flag scan — before claiming tests pass, scan the FULL output for:
       ERROR, Traceback, toolError, circuit_breaker_open, FAILED, AssertionError
     Any red-flag = the run is not clean, regardless of the final summary line.
  6a. Warning scan — also scan the FULL output for:
       PytestWarning, DeprecationWarning, UserWarning, and any other Warning lines.
     Warnings are defects, not noise. Fix ALL of them — whether introduced by this PR
     or pre-existing. Commit pre-existing warning fixes separately:
       "fix: resolve pre-existing test warning — <brief description>"
     A clean run has zero warnings AND zero failures. Note all warnings resolved in your report.
  7. Grade the PR (A/B/C/D/F) — OUTPUT GRADE FIRST before any merge command

  GRADE B — FIX-OR-TICKET PROTOCOL (apply before proceeding to STEP 6):
    A B grade means the PR is solid but has at least one specific, named concern.
    Before merging a B, you MUST choose exactly one of these two paths:

    PATH 1 — Fix it to an A (preferred):
      If the concern is a straightforward improvement (missing test assertion,
      weak docstring, minor type narrowing, a cleaner error message), fix it
      right here in the worktree, re-run mypy + targeted tests, and upgrade
      the grade to A. Commit the fix with:
        git commit -m "fix: address PR review concern — <one-line description>"
      Then push and continue to STEP 6.

    PATH 2 — Create a follow-up ticket (when fix is non-trivial):
      If the concern requires design thought, touches other files, or risks
      introducing new bugs, capture it as a GitHub issue instead of fixing
      in place. File it BEFORE merging.

  GRADE C — MANDATORY FIX PROTOCOL (never stop on a C — always fix and re-grade):
    A C grade means the quality bar was not met, but the work is recoverable.
    ⚠️  You MUST attempt to fix every C-grade issue in place. Do NOT self-destruct.
    ⚠️  "C → stop" breaks sequential merge chains and wastes all upstream work.

    Treat a C exactly like a B-PATH-1: fix it here in the worktree, re-run
    mypy + targeted tests, and re-grade. Common C-grade fixes:
      - Missing from __future__ import annotations → add it
      - Any in return type → replace with a concrete type or TypedDict
      - Missing docstrings → add them
      - dict[str, Any] crossing a module boundary → wrap in a NamedTuple/TypedDict
      - Missing downgrade() in a migration → add it
      - Missing index in upgrade() → add it
      - Weak error handling → add specific exception types

    After fixing, commit with:
      git commit -m "fix: upgrade C-grade review concerns to A — <one-line summary>"
    Then re-grade. If the re-grade is A or B → proceed to STEP 6 (merge).

    ESCALATE only if the C-grade issue is architecturally broken (wrong data model,
    missing foreign key chain, irrecoverable schema conflict). In that case:
      - DO NOT merge
      - File a GitHub issue describing exactly what must change
      - Self-destruct and report the issue URL to the coordinator
      - Never loop or block silently

      ── LABEL REFERENCE (only use labels from this list) ────────────────────
      │ bug              documentation     duplicate         enhancement       │
      │ good first issue help wanted       invalid           question          │
      │ wontfix          multimodal        performance       ai-pipeline       │
      │ muse             muse-cli          muse-hub          storpheus         │
      │ maestro-integration  mypy          cli               testing           │
      │ weekend-mvp      muse-music-extensions                                 │
      │                                                                        │
      │ ⚠️  Never invent labels (e.g. "tech-debt", "mcp", "budget",           │
      │    "security" do NOT exist). Using a missing label causes              │
      │    gh issue create to fail entirely.                                   │
      └────────────────────────────────────────────────────────────────────────

      ── TWO-STEP PATTERN (always use this — never --label on gh issue create) ──
      │ Step 1: create the issue without --label (never fails due to labels)  │
      │ Step 2: apply labels with gh issue edit (|| true = non-fatal)         │
      └────────────────────────────────────────────────────────────────────────

        FOLLOW_UP_URL=$(gh issue create \
          --title "follow-up: <specific concern from PR #<N>>" \
          --body "$(cat <<'EOF'
        ## Context
        Identified during review of PR #<N> (grade B).

        ## Concern
        <Exact description of what fell short and why it matters>

        ## Acceptance Criteria
        <What the fix must do to close this issue>

        ## Files Affected
        <List of files that need to change>
        EOF
        )")
        # Apply labels separately — each on its own line so one failure
        # doesn't block the others. Pick from the LABEL REFERENCE above only.
        gh issue edit "$FOLLOW_UP_URL" --add-label "enhancement" 2>/dev/null || true
      Report the follow-up issue URL ($FOLLOW_UP_URL) in your final report. Then proceed to STEP 6.

    ⚠️  A B grade without a fix OR a follow-up ticket URL is not acceptable.
        You must produce one artifact per B-grade concern before merging.

  8. Grade decision:
     A       → proceed to STEP 5.5 (merge order gate)
     B       → fix-or-ticket per GRADE B protocol above, then STEP 5.5
     C       → fix in place per GRADE C protocol above, re-grade, then STEP 5.5
     D or F  → DO NOT merge. File a GitHub issue. Self-destruct. Report to user.

STEP 5.5 — MERGE ORDER GATE (sequential chain safety):
  Read the MERGE_AFTER field from .agent-task:
    MERGE_AFTER=$(grep "^MERGE_AFTER=" .agent-task | cut -d= -f2)

  If MERGE_AFTER is empty or "none" → skip this step, go directly to STEP 6.

  If MERGE_AFTER is a PR number → poll until that PR is MERGED before proceeding.
  This preserves Alembic migration chains and any other ordered dependencies.

  ⚠️  Max 15 attempts × 60 s = 15 minutes. If the gate PR has not merged in
  that window it almost certainly received a D/F or had an infrastructure failure.
  DO NOT loop indefinitely — escalate and self-destruct instead.

    for i in $(seq 1 15); do
      STATE=$(gh pr view "$MERGE_AFTER" --repo "$GH_REPO" --json state --jq '.state' 2>/dev/null)
      echo "[$i/15] Gate PR #$MERGE_AFTER state: $STATE"
      if [ "$STATE" = "MERGED" ]; then
        echo "✅ Gate cleared — PR #$MERGE_AFTER is merged. Proceeding to merge."
        break
      fi
      if [ $i -eq 15 ]; then
        echo "❌ ESCALATE: PR #$MERGE_AFTER did not merge within 15 minutes."
        echo "   Possible causes: gate PR received D/F grade, infrastructure failure,"
        echo "   or requires manual intervention."
        echo "   This PR (#$N) will NOT be merged — merging out of order would break"
        echo "   the dependency chain."
        echo "   Action: fix PR #$MERGE_AFTER manually, then re-run this review agent."
        WORKTREE=$(pwd)
        cd "$REPO"
        git worktree remove --force "$WORKTREE"
        git worktree prune
        exit 1
      fi
      sleep 60
    done

STEP 6 — PRE-MERGE SYNC (only if grade is A or B):
  ⚠️  Other agents may have merged PRs while you were reviewing. Sync once more
  before merging to catch any new conflicts.

  # 1. COMMIT GUARD — commit everything before touching origin. No exceptions.
  #    An uncommitted working tree causes git merge to abort with "local changes
  #    would be overwritten." This guard prevents that entirely.
  git add -A
  git diff --cached --quiet || git commit -m "chore: stage review edits before final dev sync"

  # 2. Capture branch name FIRST — you need it for the push and delete below
  BRANCH=$(git rev-parse --abbrev-ref HEAD)

  # 3. Sync with dev
  git fetch origin
  git merge origin/dev

  If new conflicts appear after the final sync:
  - Use the CONFLICT PLAYBOOK from STEP 3 — same rules apply.
  - For markdown-only conflicts (muse_vcs.md, type_contracts.md), skip mypy.
  - For app.py or any Python file, re-run mypy before pushing.
  - If conflicts are non-trivial and introduce risk → downgrade grade to B
    and file a follow-up issue. Still merge if the overall work is solid.

  # 3. ALWAYS push the branch before merging — even if there were no conflicts.
  #    GitHub sees the REMOTE branch tip, not your local state. If another PR landed
  #    since your last sync, GitHub will reject the merge until you push the resolution.
  git push origin "$BRANCH"

  # 4. Wait for GitHub to recompute merge status after the push
  sleep 5

  Output "Approved for merge" and then run these in order:

  # 5. Squash merge — this is the ONLY valid merge strategy here.
  #    NEVER use --auto (requires branch protection rules we don't have).
  #    NEVER use --merge (wrong strategy, creates a merge commit on dev).
  #    NEVER use --delete-branch (breaks in multi-worktree setups).
       gh pr merge <N> --squash

  ── If gh pr merge still reports conflicts after the push ──────────────────
  │ GitHub sometimes needs more time to recompute merge status. Wait and retry: │
  │                                                                             │
  │   sleep 10                                                                  │
  │   gh pr merge <N> --squash                                                  │
  │                                                                             │
  │ If it STILL fails: the feature branch has diverged again (yet another PR   │
  │ landed in the gap). Re-run the full sync:                                  │
  │   git fetch origin && git merge origin/dev                                  │
  │   git push origin "$BRANCH"                                                 │
  │   sleep 5 && gh pr merge <N> --squash                                       │
  │                                                                             │
  │ After two sync+push+retry cycles with no success → stop, report the PR     │
  │ URL and the exact error, and let the user merge manually.                  │
  └─────────────────────────────────────────────────────────────────────────────

  # 6. Delete the remote branch manually (now safe — merge is done):
       git push origin --delete "$BRANCH"

  # 7. Post a fingerprint comment on the PR so every merge is permanently traceable:
       gh pr comment <N> --repo "$GH_REPO" --body "$(cat <<EOF
  🤖 **Maestro Review Fingerprint**

  | Field | Value |
  |-------|-------|
  | Role | \`pr-reviewer\` |
  | Batch | \`${BATCH_ID:-none}\` |
  | Session | \`${AGENT_SESSION}\` |
  | Grade | \`<A/B/C/D/F>\` |
  | Merged at | \`$(date -u +%Y-%m-%dT%H:%M:%SZ)\` |

  *To trace this review: search agent transcripts for session \`${AGENT_SESSION}\`
  or batch \`${BATCH_ID:-none}\`.*
  EOF
  )"

  # NOTE: Do NOT delete the local branch here — the branch is still checked out
  # in this worktree, so git will refuse. The local branch ref is cleaned up in
  # STEP 8 (SELF-DESTRUCT) AFTER the worktree is removed.

  # 7. Close every referenced issue.
  #    CLOSES_ISSUES is pre-populated from .agent-task (the coordinator extracted
  #    it at setup time). Use it directly to avoid re-parsing the PR body.
  #    ⚠️  Do NOT use `grep -o '#[0-9]*'` — it matches any #N (commit hashes,
  #    mentions, literal numbers) and silently closes the wrong issue.
  #    ⚠️  Do NOT use `while read` — the `read` builtin triggers a sandbox prompt.
       CLOSES_ISSUES=$(grep "^CLOSES_ISSUES=" .agent-task | cut -d= -f2)
       if [ -n "$CLOSES_ISSUES" ]; then
         echo "$CLOSES_ISSUES" | tr ',' '\n' | xargs -I{} gh issue close {} \
           --comment "Fixed by PR #$N." \
           --repo "$GH_REPO"
       else
         # Fallback: re-parse the PR body if CLOSES_ISSUES was empty in task file
         gh pr view "$N" --json body --jq '.body' \
           | grep -oE '[Cc]loses?\s+#[0-9]+' \
           | grep -oE '[0-9]+' \
           | xargs -I{} gh issue close {} \
               --comment "Fixed by PR #$N." \
               --repo "$GH_REPO"
       fi

  ⚠️  Never use --delete-branch with gh pr merge in a multi-worktree setup.
      gh attempts to checkout dev locally to delete the feature branch, but dev
      is already checked out in the main worktree and git will refuse.

  # 8. Mark linked issues as merged (conductor reads this as "done").
  CLOSES_ISSUES_FOR_LABEL=$(grep "^CLOSES_ISSUES=" .agent-task | cut -d= -f2)
  if [ -n "$CLOSES_ISSUES_FOR_LABEL" ]; then
    echo "$CLOSES_ISSUES_FOR_LABEL" | tr ',' '\n' | xargs -I{} sh -c \
      'gh issue edit {} --repo "$GH_REPO" --remove-label "status/pr-open" 2>/dev/null || true
       gh issue edit {} --repo "$GH_REPO" --add-label "status/merged" 2>/dev/null || true'
  fi

  # 9. Pull the merge into the main repo's local dev — so the coordinator's
  #    working copy reflects reality and the next batch starts from the true tip.
  #    This is the step that prevents "relation does not exist" DB errors when the
  #    coordinator tries to apply migrations before fetching.
  git -C "$REPO" fetch origin
  git -C "$REPO" merge origin/dev

STEP 7 — REGRESSION FEEDBACK LOOP (only if merge succeeded — skip if D/F grade):
  After a successful merge, run targeted tests against dev to detect regressions
  introduced by this batch. Any new failures become GitHub issues automatically
  and re-enter the pipeline — no human triage required.

  # Pull the latest dev (contains the just-merged PR):
  git -C "$REPO" fetch origin && git -C "$REPO" merge origin/dev

  # Run targeted tests for the files this PR touched (not the full suite):
  FILES_CHANGED_FOR_TEST=$(grep "^FILES_CHANGED=" .agent-task | cut -d= -f2)
  # Derive test file paths from FILES_CHANGED (e.g. maestro/api/routes/musehub/labels.py
  # → tests/test_musehub_labels.py). Run only those test files.
  TEST_OUTPUT=$(cd "$REPO" && docker compose exec maestro sh -c \
    "PYTHONPATH=/app pytest tests/ -v --tb=short -q 2>&1" 2>&1 | tail -30)
  echo "$TEST_OUTPUT"

  # Scan for failures:
  FAILED_TESTS=$(echo "$TEST_OUTPUT" | grep "^FAILED " | sed 's/^FAILED //')
  if [ -n "$FAILED_TESTS" ]; then
    echo "⚠️  New failures detected post-merge. Creating regression issues..."
    while IFS= read -r test_line; do
      [ -z "$test_line" ] && continue
      # Create a bug fix issue for each failing test
      BUG_URL=$(gh issue create \
        --repo "$GH_REPO" \
        --title "fix: regression — $test_line (introduced near batch merge)" \
        --body "## Regression Report

**Failing test:** \`$test_line\`
**Detected after merging:** PR #$N (batch: $(grep '^BATCH_LABEL=' .agent-task | cut -d= -f2))
**Detection method:** post-merge targeted test run in PR_REVIEW STEP 7

## Reproduction
\`\`\`bash
docker compose exec maestro pytest $test_line -v
\`\`\`

## Context
This failure was not present before this PR was merged. The most likely cause is a
side-effect of the changes in PR #$N. Start investigation there.

## Acceptance Criteria
- [ ] Test passes again
- [ ] No other tests regressed by the fix
- [ ] mypy clean after fix
")
      # Apply labels (two-step pattern — label failures are non-fatal)
      gh issue edit "$BUG_URL" --add-label "bug" 2>/dev/null || true
      # Apply the next available batch label (pipeline picks it up automatically)
      NEXT_BATCH=$(gh label list --repo "$GH_REPO" \
        --search "batch-" --json name --jq '[.[].name] | sort | last' 2>/dev/null || echo "")
      [ -n "$NEXT_BATCH" ] && \
        gh issue edit "$BUG_URL" --add-label "$NEXT_BATCH" 2>/dev/null || true
      echo "✅ Regression issue created: $BUG_URL"
    done <<< "$FAILED_TESTS"
  else
    echo "✅ No regressions detected. Post-merge test run clean."
  fi

STEP 8 — SPAWN YOUR SUCCESSOR (run this before self-destructing):

  # Read SPAWN_MODE from .agent-task to determine what to spawn next.
  # SPAWN_MODE=chain  → spawned by an engineer; spawn the next ENGINEER for the next issue
  # SPAWN_MODE=pool   → spawned by a QA VP; spawn the next REVIEWER for the next PR (legacy pool behavior)
  # (absent/empty)    → default to pool behavior
  SPAWN_MODE=$(grep "^SPAWN_MODE=" "$WORKTREE/.agent-task" 2>/dev/null | cut -d= -f2)

  if [ "$SPAWN_MODE" = "chain" ]; then
    # ── CHAIN MODE: merge happened → spawn next engineer for next unclaimed issue ──

    # Find the next open, unclaimed, htmx-tagged issue whose dependencies are met.
    NEXT_ISSUE=$(gh issue list \
      --repo "$GH_REPO" \
      --state open \
      --json number,labels \
      --jq '[.[] | select(
               (.labels | map(.name) | any(startswith("htmx/"))) and
               (.labels | map(.name) | index("agent:wip") | not)
             )] | first | .number // empty')

    # Dependency gate: only proceed if all "Depends on #NNN" references are CLOSED.
    if [ -n "$NEXT_ISSUE" ]; then
      BODY=$(gh issue view "$NEXT_ISSUE" --repo "$GH_REPO" --json body --jq '.body' 2>/dev/null || echo "")
      DEPS=$(echo "$BODY" | grep -oE 'Depends on[^#]*#[0-9]+' | grep -oE '[0-9]+')
      for dep in $DEPS; do
        DEP_STATE=$(gh issue view "$dep" --repo "$GH_REPO" --json state --jq '.state' 2>/dev/null || echo "OPEN")
        if [ "$DEP_STATE" != "CLOSED" ]; then
          echo "ℹ️  Issue #$NEXT_ISSUE blocked by open dependency #$dep — chain complete for now."
          NEXT_ISSUE=""
          break
        fi
      done
    fi

    # Guard against race: verify no branch already exists.
    if [ -n "$NEXT_ISSUE" ]; then
      if git -C "$REPO" ls-remote --exit-code origin "refs/heads/feat/issue-$NEXT_ISSUE" &>/dev/null; then
        NEXT_ISSUE=""   # another agent already claimed it
      fi
    fi

    if [ -n "$NEXT_ISSUE" ]; then
      gh issue edit "$NEXT_ISSUE" --repo "$GH_REPO" --add-label "agent:wip" 2>/dev/null || true

      NEXT_WORKTREE="$HOME/.cursor/worktrees/maestro/issue-$NEXT_ISSUE"
      git -C "$REPO" worktree add -b "feat/issue-$NEXT_ISSUE" "$NEXT_WORKTREE" origin/dev

      cat > "$NEXT_WORKTREE/.agent-task" <<TASK
TASK=issue-to-pr
ISSUE_NUMBER=$NEXT_ISSUE
BRANCH=feat/issue-$NEXT_ISSUE
WORKTREE=$NEXT_WORKTREE
ROLE=python-developer
ROLE_FILE=/Users/gabriel/dev/tellurstori/maestro/.cursor/roles/python-developer.md
BASE=dev
GH_REPO=cgcardona/maestro
CLOSES_ISSUES=$NEXT_ISSUE
BATCH_ID=${BATCH_ID:-none}
TASK

      echo "✅ Chain: spawning engineer for issue #$NEXT_ISSUE (will spawn its own reviewer when done)"
      # AGENT INSTRUCTION: call Task(prompt="Read the .agent-task file in your worktree,
      # then follow the complete Kickoff Prompt in
      # /Users/gabriel/dev/tellurstori/maestro/.cursor/PARALLEL_ISSUE_TO_PR.md.
      # GH_REPO=cgcardona/maestro  Repo: /Users/gabriel/dev/tellurstori/maestro",
      # worktree="$NEXT_WORKTREE")
    else
      echo "ℹ️  Chain complete — no unclaimed issues remaining."
    fi

  else
    # ── POOL MODE: spawned by QA VP; spawn the next REVIEWER for the next open PR ──

    NEXT_PR=$(gh pr list \
      --repo "$GH_REPO" \
      --base dev \
      --state open \
      --json number,labels \
      --jq '[.[] | select(.labels | map(.name) | index("agent:wip") | not)] | first | .number // empty')

    if [ -n "$NEXT_PR" ]; then
      gh pr edit "$NEXT_PR" --repo "$GH_REPO" --add-label "agent:wip" 2>/dev/null || true

      NEXT_BRANCH=$(gh pr view "$NEXT_PR" --repo "$GH_REPO" --json headRefName --jq .headRefName)
      NEXT_WORKTREE="$HOME/.cursor/worktrees/maestro/pr-$NEXT_PR"
      git -C "$REPO" worktree add "$NEXT_WORKTREE" "origin/$NEXT_BRANCH"

      cat > "$NEXT_WORKTREE/.agent-task" <<TASK
TASK=pr-review
PR=$NEXT_PR
BRANCH=$NEXT_BRANCH
WORKTREE=$NEXT_WORKTREE
ROLE=pr-reviewer
ROLE_FILE=/Users/gabriel/dev/tellurstori/maestro/.cursor/roles/pr-reviewer.md
BASE=dev
GH_REPO=cgcardona/maestro
BATCH_ID=${BATCH_ID:-none}
SPAWN_MODE=pool
TASK

      echo "✅ Pool: spawning replacement reviewer for PR #$NEXT_PR"
      # AGENT INSTRUCTION: call Task(prompt="Read the .agent-task file in your worktree,
      # then follow the complete Kickoff Prompt in
      # /Users/gabriel/dev/tellurstori/maestro/.cursor/PARALLEL_PR_REVIEW.md.
      # GH_REPO=cgcardona/maestro  Repo: /Users/gabriel/dev/tellurstori/maestro",
      # worktree="$NEXT_WORKTREE")
    else
      echo "ℹ️  Pool complete — no unclaimed PRs remaining."
    fi
  fi

STEP 9 — SELF-DESTRUCT (always run this after STEP 8, merge or not, early stop or not):
  WORKTREE=$(pwd)
  BRANCH_TO_DELETE=$(git rev-parse --abbrev-ref HEAD)
  cd "$REPO"
  git worktree remove --force "$WORKTREE"   # must come FIRST — branch is checked out here
  git worktree prune
  git branch -D "$BRANCH_TO_DELETE" 2>/dev/null || true  # safe now that worktree is gone

⚠️  NEVER copy files to the main repo for testing.
⚠️  NEVER start a review without completing STEP 2. Skipping the check causes
    duplicate review passes and redundant merge attempts.
⚠️  NEVER run gh pr merge without first outputting your grade.

CRITICAL: You MUST output your grade and "Approved for merge" OR "Not approved — do not merge"
BEFORE running any gh pr merge command.

Report: PR number, grade, merge status, any improvements made, follow-up issues to file.
```

---

## Grading reference

| Grade | Meaning | Action |
|-------|---------|--------|
| **A** | Production-ready. Types, tests, docs all solid. | Merge immediately. |
| **B** | Solid but has named minor concerns. | Fix in place → upgrade to A (preferred), OR file follow-up ticket → then merge. Fix commit OR issue URL required. |
| **C** | Quality bar not met but recoverable. | **Fix in place and re-grade. Never stop on a C.** Same as B-PATH-1. Escalate only if architecturally irrecoverable — file issue URL, self-destruct, report to user. |
| **D** | Unsafe, incomplete, or breaks a contract. | Do NOT merge. File GitHub issue. Self-destruct. Report issue URL to user. |
| **F** | Regression, security hole, or architectural violation. | Reject. File GitHub issue. Self-destruct. Report issue URL to user. |

---

## Before launching

### Step 0 — File overlap check (run before creating worktrees)

Before dispatching review agents in parallel, verify the PRs in this batch
do not share modified files with each other. Two agents merging PRs that touch
the same file will produce conflicts during the pre-merge re-sync.

```bash
REPO=$(git rev-parse --show-toplevel)
cd "$REPO"

echo "=== Files touched by PRs in this batch ==="
for pr in <N1> <N2> <N3>; do   # substitute actual PR numbers
  echo ""
  echo "PR #$pr:"
  gh pr diff "$pr" --name-only 2>/dev/null | sed 's/^/  /'
done
echo ""
echo "⚠️  Any file appearing under two PRs = merge conflict guaranteed."
echo "⚠️  Resolve: review the earlier PR first, merge it, then review the later one."
```

If two PRs in the batch share a file:
- Review and merge the simpler/earlier PR first.
- Then add the second PR to the next review batch (after dev has the first merged).

### Step 1 — Confirm PRs are open

```bash
gh pr list --state open
```

### Step 2 — Confirm `dev` is up to date

```bash
REPO=$(git rev-parse --show-toplevel)
git -C "$REPO" fetch origin
git -C "$REPO" merge origin/dev
```

> **Why `fetch + merge` and not `git pull`?** `git pull --rebase` fails when there are
> uncommitted changes in the main worktree. `git pull` (merge mode) can also be blocked by
> sandbox restrictions that prevent git from writing to `.git/config`. `fetch + merge` is
> always safe and never needs sandbox elevation.

### Step 3 — Run the Setup script above

Confirm worktrees appear: `git worktree list`

### Step 4 — Confirm Docker is running and the worktrees mount is live

```bash
REPO=$(git rev-parse --show-toplevel)
docker compose -f "$REPO/docker-compose.yml" ps
docker compose exec maestro ls /worktrees/
```

---

## After agents complete

### 1 — Pull dev and check GitHub

```bash
REPO=$(git rev-parse --show-toplevel)
git -C "$REPO" fetch origin
git -C "$REPO" merge origin/dev
gh pr list --state open   # any PRs the batch failed to merge?
```

### 2 — Worktree cleanup

```bash
git worktree list   # should show only the main repo
# If stale worktrees linger (agent crashed before self-destructing):
git -C "$(git rev-parse --show-toplevel)" worktree prune
```

### 3 — Main repo cleanliness ⚠️ run this every batch, no exceptions

An agent that violates the "never copy files into the main repo" rule leaves
uncommitted changes in the main working tree. These accumulate silently across
batches and create phantom diffs that are impossible to attribute.

```bash
REPO=$(git rev-parse --show-toplevel)
git -C "$REPO" status
# Must show: nothing to commit, working tree clean
```

**If dirty files are found:**

1. Check whether the work is already merged:
   ```bash
   gh pr list --state merged --json number,title --jq '.[].number' | \
     xargs -I{} gh pr diff {} --name-only 2>/dev/null | grep <filename>
   ```
2. **If already merged** → stale copies. Discard:
   ```bash
   git -C "$REPO" restore --staged .
   git -C "$REPO" restore .
   rm -f <any .bak or untracked agent artifacts>
   ```
3. **If NOT merged** → agent wrote directly to main repo. Rescue:
   ```bash
   git -C "$REPO" checkout -b fix/<description>
   git -C "$REPO" add -A
   git -C "$REPO" commit -m "feat: <description> (rescued from main repo dirty state)"
   git push origin fix/<description>
   gh pr create --base dev --head fix/<description> ...
   ```

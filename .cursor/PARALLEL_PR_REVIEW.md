# Parallel Agent Kickoff — PR Review → Grade → Merge

Each agent gets its own ephemeral worktree. Worktrees are created at kickoff,
named by PR number, and **deleted by the agent when its job is done** — whether
the PR was merged, rejected, or left for human review. The branch lives on
GitHub regardless; the local worktree is just a working directory.

---

## Architecture

```
Kickoff (coordinator)
  └─ for each PR:
       git worktree add .../pr-<N>  dev     ← fresh worktree, named by PR
       write .agent-task into it            ← task assignment, no guessing
       launch agent in that directory

Agent (per worktree)
  └─ cat .agent-task                        ← knows exactly what to do
  └─ gh pr view <N> --json state,...        ← CHECK FIRST: merged/closed/approved?
                                               if so → stop + self-destruct
  └─ gh pr checkout <N>                     ← checks out the PR branch (only if open)
  └─ review → grade → merge (or reject)
  └─ git worktree remove --force <path>     ← self-destructs when done
  └─ git -C <main-repo> worktree prune      ← cleans up the ref
```

Worktrees are **not** kept around between cycles. If an agent crashes before
cleanup, run `git worktree prune` from the main repo.

---

## Setup — run this before launching agents

Run from anywhere inside the main repo. Paths are derived automatically.

```bash
REPO=$(git rev-parse --show-toplevel)
PRTREES="$HOME/.cursor/worktrees/$(basename "$REPO")"
cd "$REPO"

# --- define PRs ---
declare -a PRS=(
  "58|feat: muse status — working tree diff, staged files, and in-progress merge display"
  "59|feat: Muse Hub issues — create and list music project issues"
  "60|feat: muse open / muse play — CLI artifact preview and local playback"
)

# --- create worktrees + task files ---
for entry in "${PRS[@]}"; do
  NUM="${entry%%|*}"
  TITLE="${entry##*|}"
  WT="$PRTREES/pr-$NUM"
  git worktree add "$WT" dev
  printf "WORKFLOW=pr-review\nPR_NUMBER=%s\nPR_TITLE=%s\nPR_URL=https://github.com/cgcardona/maestro/pull/%s\n" \
    "$NUM" "$TITLE" "$NUM" > "$WT/.agent-task"
  echo "✅ worktree pr-$NUM ready"
done

git worktree list
```

After running this, open one Cursor composer window per worktree, each rooted
in its `pr-<N>` directory, and paste the Kickoff Prompt below.

---

## Environment (agents read this first)

**You are running inside a Cursor worktree.** Your working directory is NOT the main repo.

```bash
# Derive paths — run these at the start of your session
REPO=$(git worktree list | head -1 | awk '{print $1}')   # main repo
WTNAME=$(basename "$(pwd)")                               # this worktree's name
# Docker path to your worktree: /worktrees/$WTNAME
```

| Item | Value |
|------|-------|
| Your worktree root | current directory (contains `.agent-task`) |
| Main repo | first entry of `git worktree list` |
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
cd "$REPO" && docker compose exec maestro sh -c \
  "PYTHONPATH=/worktrees/$WTNAME mypy /worktrees/$WTNAME/maestro/ /worktrees/$WTNAME/tests/"

# pytest (specific file)
cd "$REPO" && docker compose exec maestro sh -c \
  "PYTHONPATH=/worktrees/$WTNAME pytest /worktrees/$WTNAME/tests/path/to/test_file.py -v"
```

**⚠️ NEVER copy files into the main repo** for testing purposes. That pollutes
the `dev` branch with uncommitted changes that don't belong there.

> **Alembic exception:** If the PR includes a migration, verify migration correctness by
> reading the file rather than applying it during review.

---

## Kickoff Prompt

```
PARALLEL AGENT COORDINATION — PR REVIEW

STEP 0 — READ YOUR TASK:
  cat .agent-task
  This file tells you your PR number, title, and URL. Substitute your actual
  PR number wherever you see <N> below.

STEP 1 — DERIVE PATHS:
  REPO=$(git worktree list | head -1 | awk '{print $1}')
  WTNAME=$(basename "$(pwd)")
  # Your worktree is live in Docker at /worktrees/$WTNAME — NO file copying needed.
  # All docker compose commands: cd "$REPO" && docker compose exec maestro <cmd>

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
  gh pr checkout <N>
  git fetch origin && git merge origin/dev

STEP 4 — REVIEW:
  Read and follow every step in .github/PR_REVIEW_PROMPT.md exactly.
  1. Context — read PR description, referenced issue, commits, files changed
  2. Deep review — work through all applicable checklist sections (3a–3j)
  3. Add/fix tests if weak or missing
  4. Run mypy and tests (Docker-native, using $REPO and $WTNAME from STEP 1).
     ⚠️  Never pipe mypy/pytest through grep/head/tail — full output, exit code is authoritative.
  5. Red-flag scan — before claiming tests pass, scan the FULL output for:
       ERROR, Traceback, toolError, circuit_breaker_open, FAILED, AssertionError
     Any red-flag = the run is not clean, regardless of the final summary line.
  6. Grade the PR (A/B/C/D/F) — OUTPUT GRADE FIRST before any merge command
  7. Merge ONLY if grade is A or B and you have written "Approved for merge"
  8. After merge: close the referenced issue

STEP 5 — SELF-DESTRUCT (always run this, merge or not, early stop or not):
  WORKTREE=$(pwd)
  cd "$REPO"
  git worktree remove --force "$WORKTREE"
  git worktree prune

⚠️  NEVER copy files to the main repo for testing.
⚠️  NEVER start a review without completing STEP 2. Skipping the check causes
    duplicate review passes and redundant merge attempts.

CRITICAL: You MUST output your grade and "Approved for merge" OR "Not approved — do not merge"
BEFORE running any gh pr merge command.

Report: PR number, grade, merge status, any improvements made, follow-up issues to file.
```

---

## Grading reference

| Grade | Meaning | Action |
|-------|---------|--------|
| **A** | Production-ready. Types, tests, docs all solid. | Merge immediately |
| **B** | Solid fix, minor concerns noted. | Merge, file follow-up issues |
| **C** | Fix works but quality bar not met. | Do NOT merge. State what must change. |
| **D** | Unsafe, incomplete, or breaks a contract. | Do NOT merge. |
| **F** | Regression, security hole, or architectural violation. | Reject. |

---

## Before launching

1. Confirm PRs are open: `gh pr list --state open`
2. Run the Setup script above — confirm worktrees appear: `git worktree list`
3. Confirm Docker is running and the worktrees mount is live:
   ```bash
   REPO=$(git rev-parse --show-toplevel)
   docker compose -f "$REPO/docker-compose.yml" ps
   docker compose exec maestro ls /worktrees/
   ```
4. Confirm `dev` is up to date:
   ```bash
   git -C "$(git rev-parse --show-toplevel)" pull origin dev
   ```

---

## After agents complete

- Check GitHub for merged PRs and closed issues.
- Pull `dev` locally: `git -C "$(git rev-parse --show-toplevel)" pull origin dev`
- Verify no stale worktrees remain: `git worktree list` — should show only the main repo.
  If any linger (agent crashed before cleanup):
  ```bash
  git -C "$(git rev-parse --show-toplevel)" worktree prune
  ```
- Verify main repo is clean: `git -C "$(git rev-parse --show-toplevel)" status`
  Must show **nothing to commit, working tree clean**. If not, clean up:
  ```bash
  REPO=$(git rev-parse --show-toplevel)
  git -C "$REPO" restore --staged .
  git -C "$REPO" restore .
  ```

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
  └─ gh pr checkout <N>                     ← checks out the PR branch
  └─ review → grade → merge (or reject)
  └─ git worktree remove --force <path>     ← self-destructs when done
  └─ git -C <main-repo> worktree prune      ← cleans up the ref
```

Worktrees are **not** kept around between cycles. If an agent crashes before
cleanup, run `git worktree prune` from the main repo.

---

## Setup — run this before launching agents

Replace the PR list as needed. This creates one worktree per PR and writes the
task assignment file into each.

```bash
REPO=/Users/gabriel/dev/tellurstori/maestro
PRTREES=/Users/gabriel/.cursor/worktrees/maestro
cd $REPO

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
  printf "PR_NUMBER=%s\nPR_TITLE=%s\nPR_URL=https://github.com/cgcardona/maestro/pull/%s\n" \
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

| Item | Value |
|------|-------|
| Your worktree root | the directory you opened in (contains `.agent-task`) |
| Main repo | `/Users/gabriel/dev/tellurstori/maestro` |
| Docker compose location | `/Users/gabriel/dev/tellurstori/maestro` |
| Your worktree inside Docker | `/worktrees/pr-<N>/` |

**All `docker compose exec` commands must be run from the main repo:**
```bash
cd /Users/gabriel/dev/tellurstori/maestro && docker compose exec maestro <cmd>
```

### Docker sees your worktree directly — no file copying needed

`docker-compose.override.yml` bind-mounts the entire worktrees directory:

```
/Users/gabriel/.cursor/worktrees/maestro  →  /worktrees  (inside container)
```

After checking out the PR branch, your worktree's code is **immediately live
inside the container at `/worktrees/pr-<N>/`**. Run mypy and tests directly:

```bash
# mypy
cd /Users/gabriel/dev/tellurstori/maestro && \
  docker compose exec maestro sh -c \
    "PYTHONPATH=/worktrees/pr-<N> mypy /worktrees/pr-<N>/maestro/ /worktrees/pr-<N>/tests/"

# pytest (specific file)
cd /Users/gabriel/dev/tellurstori/maestro && \
  docker compose exec maestro sh -c \
    "PYTHONPATH=/worktrees/pr-<N> pytest /worktrees/pr-<N>/tests/path/to/test_file.py -v"
```

**⚠️ NEVER copy files into the main repo** (`/Users/gabriel/dev/tellurstori/maestro`) for
testing purposes. That pollutes the `dev` branch with uncommitted changes that don't belong there.

> **Alembic exception:** If the PR includes a migration, verify migration correctness by
> reading the file rather than applying it during review.

---

## Kickoff Prompt

```
PARALLEL AGENT COORDINATION — PR REVIEW

STEP 0 — READ YOUR TASK:
  cat .agent-task
  This file tells you your PR number, title, and URL. All instructions below
  use <N> as a placeholder — substitute your actual PR number everywhere.

STEP 1 — ENVIRONMENT:
  - Main repo: /Users/gabriel/dev/tellurstori/maestro
  - All docker compose commands: cd /Users/gabriel/dev/tellurstori/maestro && docker compose exec maestro <cmd>
  - Your worktree is live in Docker at /worktrees/pr-<N>/ — NO file copying needed.

STEP 2 — CHECKOUT & SYNC:
  cd <your worktree root>
  gh pr checkout <N>
  git fetch origin && git merge origin/dev

STEP 3 — REVIEW:
  Read and follow every step in .github/PR_REVIEW_PROMPT.md exactly.
  1. Context — read PR description, referenced issue, commits, files changed
  2. Deep review — work through all applicable checklist sections (3a–3j)
  3. Add/fix tests if weak or missing
  4. Run mypy and tests (Docker-native, per STEP 1 above)
  5. Grade the PR (A/B/C/D/F) — OUTPUT GRADE FIRST before any merge command
  6. Merge ONLY if grade is A or B and you have written "Approved for merge"
  7. After merge: close the referenced issue

STEP 4 — SELF-DESTRUCT (always run this, merge or not):
  WORKTREE=$(pwd)
  cd /Users/gabriel/dev/tellurstori/maestro
  git worktree remove --force "$WORKTREE"
  git worktree prune

⚠️  NEVER copy files to /Users/gabriel/dev/tellurstori/maestro for testing.

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
3. Confirm Docker is running and worktrees mount is live:
   ```bash
   docker compose -f /Users/gabriel/dev/tellurstori/maestro/docker-compose.yml ps
   docker compose exec maestro ls /worktrees/
   ```
4. Confirm `dev` is up to date: `git -C /Users/gabriel/dev/tellurstori/maestro pull origin dev`

---

## After agents complete

- Check GitHub for merged PRs and closed issues.
- Pull `dev` locally: `git -C /Users/gabriel/dev/tellurstori/maestro pull origin dev`
- Verify no stale worktrees remain: `git worktree list` — should show only the main repo.
  If any linger (agent crashed before cleanup):
  ```bash
  git -C /Users/gabriel/dev/tellurstori/maestro worktree prune
  ```
- Verify main repo is clean: `git -C /Users/gabriel/dev/tellurstori/maestro status`
  Must show **nothing to commit, working tree clean**. If not, agents copied files — clean up:
  ```bash
  git -C /Users/gabriel/dev/tellurstori/maestro restore --staged .
  git -C /Users/gabriel/dev/tellurstori/maestro restore .
  ```

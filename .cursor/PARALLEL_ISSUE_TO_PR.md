# Parallel Agent Kickoff — Issue → PR

Each agent gets its own ephemeral worktree. Worktrees are created at kickoff,
named by issue number, and **deleted by the agent when its job is done**.
The branch and PR live on GitHub regardless — the local worktree is just a
working directory.

---

## Architecture

```
Kickoff (coordinator)
  └─ for each issue:
       git worktree add .../issue-<N>  dev  ← fresh worktree, named by issue
       write .agent-task into it            ← task assignment, no guessing
       launch agent in that directory

Agent (per worktree)
  └─ cat .agent-task                        ← knows exactly what to do
  └─ git checkout -b fix/<description>      ← creates feature branch
  └─ implement → mypy → tests → commit → push → gh pr create
  └─ WORKTREE=$(pwd)                        ← self-destructs when done
     cd "$REPO"
     git worktree remove --force "$WORKTREE"
     git worktree prune
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

# --- define issues (confirmed independent — zero file overlap) ---
declare -a ISSUES=(
  "35|feat: \`muse merge\` — fast-forward and 3-way merge with path-level conflict detection"
  "37|feat: Maestro stress test → muse-work/ output contract with muse-batch.json manifest"
  "40|feat: Muse Hub push/pull sync protocol — batch commit and object transfer"
  "41|feat: Muse Hub pull requests — create, list, and merge PRs between branches"
  "47|feat: Muse Hub JWT auth integration — CLI token storage and Hub request authentication"
)

# --- create worktrees + task files ---
for entry in "${ISSUES[@]}"; do
  NUM="${entry%%|*}"
  TITLE="${entry##*|}"
  WT="$PRTREES/issue-$NUM"
  git worktree add "$WT" dev
  printf "WORKFLOW=issue-to-pr\nISSUE_NUMBER=%s\nISSUE_TITLE=%s\nISSUE_URL=https://github.com/cgcardona/maestro/issues/%s\n" \
    "$NUM" "$TITLE" "$NUM" > "$WT/.agent-task"
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
the container. After creating your feature branch, your worktree's code is
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
the `dev` branch with uncommitted changes.

> **Alembic exception:** `alembic revision --autogenerate` must run from the main repo
> because it needs a live DB connection. After generating, immediately `git mv` the
> migration file into your worktree and delete the copy from the main repo.

---

## Kickoff Prompt

```
PARALLEL AGENT COORDINATION — ISSUE TO PR

STEP 0 — READ YOUR TASK:
  cat .agent-task
  This file tells you your issue number, title, and URL. Substitute your actual
  issue number wherever you see <N> below.

STEP 1 — DERIVE PATHS:
  REPO=$(git worktree list | head -1 | awk '{print $1}')
  WTNAME=$(basename "$(pwd)")
  # Your worktree is live in Docker at /worktrees/$WTNAME — NO file copying needed.
  # All docker compose commands: cd "$REPO" && docker compose exec maestro <cmd>

STEP 2 — IMPLEMENT:
  Read and follow every step in .github/CREATE_PR_PROMPT.md exactly.
  Steps: issue analysis → branch (from dev) → implement → mypy → tests → commit → docs → PR.

  mypy:
    cd "$REPO" && docker compose exec maestro sh -c \
      "PYTHONPATH=/worktrees/$WTNAME mypy /worktrees/$WTNAME/maestro/ /worktrees/$WTNAME/tests/"

  pytest:
    cd "$REPO" && docker compose exec maestro sh -c \
      "PYTHONPATH=/worktrees/$WTNAME pytest /worktrees/$WTNAME/tests/path/to/test_file.py -v"

STEP 3 — SELF-DESTRUCT (always run this after the PR is open):
  WORKTREE=$(pwd)
  cd "$REPO"
  git worktree remove --force "$WORKTREE"
  git worktree prune

⚠️  NEVER copy files to the main repo for testing.

Report: issue number, PR URL, fix summary, tests added, any protocol changes requiring handoff.
```

---

## Before launching

1. Confirm issues are open and independent (zero file overlap between them):
   `gh issue list --state open`
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

- Review opened PRs on GitHub: `gh pr list --state open`
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
- PRs are immediately available for the **PARALLEL_PR_REVIEW.md** workflow.

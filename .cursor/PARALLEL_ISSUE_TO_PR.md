# Parallel Agent Kickoff — Issue → PR (N agents)

Coordination template for **CREATE_PR_PROMPT.md**.
Each agent claims a task number, picks up the matching GitHub issue, implements the fix, and opens a PR.

---

## Environment (read before doing anything else)

**You are running inside a Cursor worktree.** Your working directory is NOT the main repo.

| Item | Value |
|------|-------|
| Your worktree root | `/Users/gabriel/.cursor/worktrees/maestro/<id>/` (wherever you are) |
| Main repo | `/Users/gabriel/dev/tellurstori/maestro` |
| Docker compose location | `/Users/gabriel/dev/tellurstori/maestro` |
| Your worktree inside Docker | `/worktrees/<id>/` |

**All `docker compose exec` commands must be run from the main repo:**
```bash
cd /Users/gabriel/dev/tellurstori/maestro && docker compose exec maestro <cmd>
```

### Docker sees your worktree directly — no file copying needed

`docker-compose.override.yml` bind-mounts the entire worktrees directory:

```
/Users/gabriel/.cursor/worktrees/maestro  →  /worktrees  (inside container)
```

So your worktree at `/Users/gabriel/.cursor/worktrees/maestro/eas/` is live inside the
container at `/worktrees/eas/`. **Run mypy and tests directly against your worktree path**
by prepending `PYTHONPATH=/worktrees/<id>`:

```bash
# mypy
cd /Users/gabriel/dev/tellurstori/maestro && \
  docker compose exec maestro sh -c "PYTHONPATH=/worktrees/<id> mypy /worktrees/<id>/maestro/ /worktrees/<id>/tests/"

# pytest (specific file)
cd /Users/gabriel/dev/tellurstori/maestro && \
  docker compose exec maestro sh -c "PYTHONPATH=/worktrees/<id> pytest /worktrees/<id>/tests/path/to/test_file.py -v"
```

**⚠️ NEVER copy files into the main repo** (`/Users/gabriel/dev/tellurstori/maestro`) for
testing purposes. The bind-mount makes that unnecessary, and leftover copies pollute the `dev`
branch with uncommitted changes that don't belong there.

> **Alembic exception:** `alembic revision --autogenerate` must still run from the main repo
> because Alembic writes the migration file to disk and needs a live DB connection. After
> generating, immediately `git mv` the migration file into your worktree and delete the copy
> from the main repo before committing.

---

## Self-assignment

Read `.agent-id` in your working directory. If it doesn't exist (the setup script may not have run in worktrees), determine your task number from your worktree directory name:
- First worktree alphabetically (e.g. `eas`) → Agent **1**
- Second worktree (e.g. `jzx`) → Agent **2**
- Third worktree (e.g. `wfk`) → Agent **3**
- And so on

Write your assigned number to `.agent-id` before proceeding:
```bash
echo "1" > .agent-id   # or 2, 3, etc.
```

---

## Kickoff Prompt

```
PARALLEL AGENT COORDINATION

ENVIRONMENT SETUP (do this first):
1. Your worktree is at your current working directory (run `pwd` to confirm).
   Determine your <id> — the last path component (e.g. "eas", "jzx", "wfk").
2. The main repo is at /Users/gabriel/dev/tellurstori/maestro.
3. All docker compose commands: cd /Users/gabriel/dev/tellurstori/maestro && docker compose exec maestro <cmd>
4. Your worktree IS visible inside Docker at /worktrees/<id>/ — NO file copying required.

SELF-ASSIGNMENT:
Read .agent-id in your working directory. If missing, use your worktree folder name:
- first alphabetically (e.g. eas) → 1
- second (e.g. jzx) → 2
- third (e.g. wfk) → 3
Write it: echo "N" > .agent-id

TASKS (execute ONLY the task matching your number):

**Agent 1:** https://github.com/cgcardona/maestro/issues/ISSUE_NUMBER_1
ISSUE_TITLE_1

**Agent 2:** https://github.com/cgcardona/maestro/issues/ISSUE_NUMBER_2
ISSUE_TITLE_2

**Agent 3:** https://github.com/cgcardona/maestro/issues/ISSUE_NUMBER_3
ISSUE_TITLE_3

VERIFICATION (Docker-native — no copying):
Replace <id> with your worktree folder name (e.g. eas, jzx, wfk).

  mypy:
    cd /Users/gabriel/dev/tellurstori/maestro && \
    docker compose exec maestro sh -c \
      "PYTHONPATH=/worktrees/<id> mypy /worktrees/<id>/maestro/ /worktrees/<id>/tests/"

  pytest (specific file):
    cd /Users/gabriel/dev/tellurstori/maestro && \
    docker compose exec maestro sh -c \
      "PYTHONPATH=/worktrees/<id> pytest /worktrees/<id>/tests/path/to/test_file.py -v"

⚠️  NEVER copy files to /Users/gabriel/dev/tellurstori/maestro for testing.
    That pollutes the dev branch. Your worktree is already live in Docker.

⚠️  Alembic migrations only: run `alembic revision --autogenerate` from the main repo,
    then immediately move the generated file into your worktree and delete the main-repo copy.

WORKFLOW:
Read and follow every step in .github/CREATE_PR_PROMPT.md exactly.
Steps: issue analysis → branch (from dev) → implement → mypy → tests → commit → docs → PR via gh pr create.

Report: agent ID, PR URL, fix summary, tests added, any protocol changes.
```

---

## Before launching

1. Fill in `ISSUE_NUMBER_1/2/3` and `ISSUE_TITLE_1/2/3` above with your chosen decoupled issues.
2. Confirm issues are **not sequential and not dependent** on each other (zero file overlap).
3. Confirm Docker is running and the worktrees mount is live:
   ```bash
   docker compose -f /Users/gabriel/dev/tellurstori/maestro/docker-compose.yml ps
   docker compose exec maestro ls /worktrees/
   ```
4. Confirm `dev` branch is up to date: `git -C /Users/gabriel/dev/tellurstori/maestro pull origin dev`

---

## After agents complete

1. Click **Apply** on each worktree card in Cursor (or review each PR on GitHub separately).
2. Run `git worktree list` to see all active worktrees.
3. Run `git -C /Users/gabriel/dev/tellurstori/maestro status` — it must show **nothing to commit, working tree clean**.
   If it shows any changes: agents copied files instead of using the bind-mount. Run:
   ```bash
   git -C /Users/gabriel/dev/tellurstori/maestro restore --staged .
   git -C /Users/gabriel/dev/tellurstori/maestro restore .
   # Then delete any new untracked files left behind (check git status output)
   ```
4. Run `git -C /Users/gabriel/dev/tellurstori/maestro pull origin dev` after merging.

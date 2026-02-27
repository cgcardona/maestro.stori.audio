# Parallel Agent Kickoff — Issue → PR (N agents)

Coordination template for **CREATE_PR_PROMPT.md**.
Each agent claims a task number, picks up the matching GitHub issue, implements the fix, and opens a PR.

---

## Environment (read before doing anything else)

**You are running inside a Cursor worktree.** Your working directory is NOT the main repo.

| Item | Value |
|------|-------|
| Your worktree root | `/Users/gabriel/.cursor/worktrees/maestro/<id>/` (wherever you are) |
| Main repo (bind-mounted into Docker) | `/Users/gabriel/dev/tellurstori/maestro` |
| Docker compose location | `/Users/gabriel/dev/tellurstori/maestro` |

**All `docker compose exec` commands must be run from the main repo:**
```bash
cd /Users/gabriel/dev/tellurstori/maestro && docker compose exec maestro <cmd>
```

**Bind-mount caveat:** Docker only sees files in the main repo's `maestro/` directory.
- For files you create in this worktree that are NOT yet in the main repo, use `docker cp` to stage them into the container before running mypy/tests:
  ```bash
  docker cp <worktree-path>/maestro/path/to/file.py maestro-app:/app/maestro/path/to/file.py
  ```
- For modified existing files: temporarily copy them to the main repo, run mypy/tests, then revert.

---

## Self-assignment

Read `.agent-id` in your working directory. If it doesn't exist (the setup script may not have run in worktrees), determine your task number from your worktree directory name:
- First worktree alphabetically (e.g. `auu`) → Agent **1**
- Second worktree (e.g. `iip`) → Agent **2**
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
2. The main repo and Docker bind-mount are at /Users/gabriel/dev/tellurstori/maestro.
3. All docker compose commands: cd /Users/gabriel/dev/tellurstori/maestro && docker compose exec maestro <cmd>
4. New files you create in the worktree are NOT visible in Docker automatically.
   Use: docker cp <your-file> maestro-app:/app/maestro/<path> before running mypy.

SELF-ASSIGNMENT:
Read .agent-id in your working directory. If missing, use your worktree folder name:
- first alphabetically (e.g. auu) → 1
- second (e.g. iip) → 2
Write it: echo "N" > .agent-id

TASKS (execute ONLY the task matching your number):

**Agent 1:** https://github.com/cgcardona/maestro/issues/ISSUE_NUMBER_1
ISSUE_TITLE_1

**Agent 2:** https://github.com/cgcardona/maestro/issues/ISSUE_NUMBER_2
ISSUE_TITLE_2

WORKFLOW:
Read and follow every step in .github/CREATE_PR_PROMPT.md exactly.
Steps: issue analysis → branch (from dev) → implement → mypy → tests → run tests → commit → docs → PR via gh pr create.

Report: agent ID, PR URL, fix summary, tests added, any protocol changes.
```

---

## Before launching

1. Fill in `ISSUE_NUMBER_1`, `ISSUE_TITLE_1`, etc. above with your chosen decoupled issues.
2. Confirm issues are **not sequential and not dependent** on each other.
3. Confirm `dev` branch is up to date: `git -C /Users/gabriel/dev/tellurstori/maestro pull origin dev`
4. Confirm Docker is running: `docker compose -f /Users/gabriel/dev/tellurstori/maestro/docker-compose.yml ps`

---

## After agents complete

1. Click **Apply** on each worktree card in Cursor (or review each PR on GitHub separately).
2. Run `git worktree list` to see all active worktrees.
3. Run `git -C /Users/gabriel/dev/tellurstori/maestro pull origin dev` after merging.

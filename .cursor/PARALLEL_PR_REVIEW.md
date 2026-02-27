# Parallel Agent Kickoff — PR Review → Grade → Merge (N agents)

Coordination template for **PR_REVIEW_PROMPT.md**.
Each agent claims a task number, reviews its assigned PR, grades it, and merges if approved.

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

**Checkout the PR branch into YOUR worktree**, not the main repo:
```bash
cd /Users/gabriel/.cursor/worktrees/maestro/<id>
gh pr checkout <pr-number>
git fetch origin
git merge origin/dev
```

### Docker sees your worktree directly — no file copying needed

`docker-compose.override.yml` bind-mounts the entire worktrees directory:

```
/Users/gabriel/.cursor/worktrees/maestro  →  /worktrees  (inside container)
```

After checking out the PR branch, your worktree's code is **immediately live inside the
container at `/worktrees/<id>/`**. Run mypy and tests against it directly:

```bash
# mypy
cd /Users/gabriel/dev/tellurstori/maestro && \
  docker compose exec maestro sh -c "PYTHONPATH=/worktrees/<id> mypy /worktrees/<id>/maestro/ /worktrees/<id>/tests/"

# pytest (specific file)
cd /Users/gabriel/dev/tellurstori/maestro && \
  docker compose exec maestro sh -c "PYTHONPATH=/worktrees/<id> pytest /worktrees/<id>/tests/path/to/test_file.py -v"
```

**⚠️ NEVER copy files into the main repo** (`/Users/gabriel/dev/tellurstori/maestro`) for
testing purposes. That pollutes the `dev` branch with uncommitted changes that don't belong there.

> **Alembic exception:** If the PR includes a migration, run
> `alembic upgrade head` from the main repo only after temporarily copying the migration file
> in. Revert the copy immediately after upgrading. Better: verify migration correctness by
> reading the file rather than applying it during review.

---

## Self-assignment

Read `.agent-id` in your working directory. If it doesn't exist, determine your task number from your worktree directory name:
- First worktree alphabetically (e.g. `eas`) → Agent **1**
- Second worktree (e.g. `jzx`) → Agent **2**
- Third worktree (e.g. `wfk`) → Agent **3**
- And so on

Write your assigned number to `.agent-id`:
```bash
echo "1" > .agent-id
```

---

## Kickoff Prompt

```
PARALLEL AGENT COORDINATION

ENVIRONMENT SETUP (do this first):
1. Run `pwd` to confirm your worktree path. Note your <id> (last path component, e.g. eas, jzx, wfk).
2. Main repo: /Users/gabriel/dev/tellurstori/maestro
3. All docker compose commands: cd /Users/gabriel/dev/tellurstori/maestro && docker compose exec maestro <cmd>
4. Checkout the PR into YOUR worktree (not the main repo):
     cd /Users/gabriel/.cursor/worktrees/maestro/<id>
     gh pr checkout <pr-number>
     git fetch origin && git merge origin/dev
5. Your worktree is live in Docker at /worktrees/<id>/ — NO file copying needed.
   Run mypy/tests:
     cd /Users/gabriel/dev/tellurstori/maestro && \
     docker compose exec maestro sh -c \
       "PYTHONPATH=/worktrees/<id> mypy /worktrees/<id>/maestro/ /worktrees/<id>/tests/"
     docker compose exec maestro sh -c \
       "PYTHONPATH=/worktrees/<id> pytest /worktrees/<id>/tests/path/to/test_file.py -v"

⚠️  NEVER copy files to /Users/gabriel/dev/tellurstori/maestro for testing.

SELF-ASSIGNMENT:
Read .agent-id in your working directory. If missing, use your worktree folder name:
- first alphabetically (e.g. eas) → 1
- second (e.g. jzx) → 2
- third (e.g. wfk) → 3
Write it: echo "N" > .agent-id

TASKS (execute ONLY the task matching your number):

**Agent 1:** https://github.com/cgcardona/maestro/pull/PR_NUMBER_1
PR_TITLE_1

**Agent 2:** https://github.com/cgcardona/maestro/pull/PR_NUMBER_2
PR_TITLE_2

**Agent 3:** https://github.com/cgcardona/maestro/pull/PR_NUMBER_3
PR_TITLE_3

WORKFLOW:
Read and follow every step in .github/PR_REVIEW_PROMPT.md exactly.
Steps:
1. Context — read PR description, referenced issue, commits, files changed
2. Checkout & sync — gh pr checkout <pr-number>, merge origin/dev
3. Deep review — work through all applicable checklist sections (3a–3j)
4. Add/fix tests if weak or missing
5. Run mypy and tests (Docker-native, see step 5 above)
6. Grade the PR (A/B/C/D/F) — OUTPUT GRADE FIRST before any merge command
7. Merge ONLY if grade is A or B and you have written "Approved for merge"
8. After merge: close the referenced issue, clean up local branch

CRITICAL: You MUST output your grade and "Approved for merge" OR "Not approved — do not merge"
BEFORE running any gh pr merge command.

Report: agent ID, PR number, grade, merge status, any improvements made, follow-up issues to file.
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

1. Fill in `PR_NUMBER_1/2/3` and `PR_TITLE_1/2/3` above.
2. Confirm PRs are open: `gh pr list --state open`
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
- Run `git worktree list` — Cursor will clean up worktrees automatically on the next cycle.
- Run `git -C /Users/gabriel/dev/tellurstori/maestro status` — must show **nothing to commit, working tree clean**.
  If not, agents copied files instead of using the bind-mount. Clean up with:
  ```bash
  git -C /Users/gabriel/dev/tellurstori/maestro restore --staged .
  git -C /Users/gabriel/dev/tellurstori/maestro restore .
  # Then delete any new untracked files shown in git status
  ```

# Parallel Agent Kickoff — PR Review → Grade → Merge (N agents)

Coordination template for **PR_REVIEW_PROMPT.md**.
Each agent claims a task number, reviews its assigned PR, grades it, and merges if approved.

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

**Checkout the PR branch into YOUR worktree**, not the main repo:
```bash
# In your worktree directory:
gh pr checkout <pr-number>
git fetch origin
git merge origin/dev
```

**If you need to run mypy/tests on PR branch code**, the bind-mount still applies:
- New files in the PR that aren't in main repo need `docker cp` to the container, OR
- Temporarily copy modified files to `/Users/gabriel/dev/tellurstori/maestro/maestro/`, run tests, revert.

---

## Self-assignment

Read `.agent-id` in your working directory. If it doesn't exist, determine your task number from your worktree directory name:
- First worktree alphabetically (e.g. `auu`) → Agent **1**
- Second worktree (e.g. `iip`) → Agent **2**

Write your assigned number to `.agent-id`:
```bash
echo "1" > .agent-id
```

---

## Kickoff Prompt (pre-filled for PRs #56 and #57)

```
PARALLEL AGENT COORDINATION

ENVIRONMENT SETUP (do this first):
1. Run `pwd` to confirm your worktree path.
2. Main repo and Docker bind-mount: /Users/gabriel/dev/tellurstori/maestro
3. All docker compose commands: cd /Users/gabriel/dev/tellurstori/maestro && docker compose exec maestro <cmd>
4. Checkout the PR into YOUR worktree (not the main repo):
   gh pr checkout <pr-number>
   git fetch origin && git merge origin/dev
5. To run mypy/tests on new files not yet in main repo:
   docker cp <your-file> maestro-app:/app/maestro/<path>
   Then run: cd /Users/gabriel/dev/tellurstori/maestro && docker compose exec maestro mypy <path>

SELF-ASSIGNMENT:
Read .agent-id in your working directory. If missing, use your worktree folder name:
- first alphabetically (e.g. auu) → 1
- second (e.g. iip) → 2
Write it: echo "N" > .agent-id

TASKS (execute ONLY the task matching your number):

**Agent 1:** https://github.com/cgcardona/maestro/pull/56
feat: repo-root detection utility — public repo.py, MuseNotARepoError, standard error message

**Agent 2:** https://github.com/cgcardona/maestro/pull/57
feat: Muse Hub FastAPI backend — repos, branches, commits endpoints

WORKFLOW:
Read and follow every step in .github/PR_REVIEW_PROMPT.md exactly.
Steps:
1. Context — read PR description, referenced issue, commits, files changed
2. Checkout & sync — gh pr checkout <pr-number>, merge origin/dev
3. Deep review — work through all applicable checklist sections (3a–3j)
4. Add/fix tests if weak or missing
5. Run mypy and tests
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

1. Confirm PRs are open: `gh pr list --state open`
2. Confirm Docker is running: `docker compose -f /Users/gabriel/dev/tellurstori/maestro/docker-compose.yml ps`
3. Confirm `dev` is up to date: `git -C /Users/gabriel/dev/tellurstori/maestro pull origin dev`

---

## After agents complete

- Check GitHub for merged PRs and closed issues.
- Pull `dev` locally: `git -C /Users/gabriel/dev/tellurstori/maestro pull origin dev`
- Run `git worktree list` — Cursor will clean up worktrees automatically on the next cycle.

---

## Reusable template (blank — fill in for future review batches)

Replace the TASKS block above with:

```
**Agent 1:** https://github.com/cgcardona/maestro/pull/PR_NUMBER_1
PR_TITLE_1

**Agent 2:** https://github.com/cgcardona/maestro/pull/PR_NUMBER_2
PR_TITLE_2
```

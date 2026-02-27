# Parallel Agent Kickoff — 2 Agents

Use this prompt when launching **2× parallel agents** in Cursor. Each agent will read its `.agent-id` (1 or 2), work on the matching issue, and follow the full PR workflow from `.github/CREATE_PR_PROMPT.md`.

---

## Setup

1. Ensure `.cursor/worktrees.json` exists (coordination script assigns task numbers 1–2).
2. In Cursor, start **2 parallel agents** (Cmd+Shift+P → "Start Parallel Agents" or equivalent, choose 2×).
3. Paste the prompt below.

---

## Kickoff Prompt

```
PARALLEL AGENT COORDINATION

First, read the .agent-id file in your working directory. If it doesn't exist yet, wait a few seconds and try again (the setup script may still be running).

TASKS (execute ONLY the task that matches your .agent-id number):

**Agent 1:** https://github.com/cgcardona/maestro/issues/46
feat: repo-root detection utility — `find_repo_root()` shared across all Muse CLI commands.

**Agent 2:** https://github.com/cgcardona/maestro/issues/39
feat: Muse Hub FastAPI backend — repos, branches, commits endpoints.

---

FOLLOW THE FULL WORKFLOW

Read and follow every step in `.github/CREATE_PR_PROMPT.md`. Do not skip any step. This includes:

1. Issue analysis
2. Branch setup (base: dev, branch: fix/<short-desc> or feat/<short-desc>)
3. Implementation
4. mypy (run before tests)
5. Tests (regression + unit + integration as applicable)
6. Run relevant tests
7. Handoff prompt (if protocol/SSE changed)
8. Commit & push
9. Update docs
10. Create PR via `gh pr create` using the exact format from CREATE_PR_PROMPT.md Step 10

Report your agent ID, PR URL, and completion summary when done.
```

---

## Issues Chosen (Decoupled)

| Agent | Issue | Title | Why decoupled |
|-------|-------|-------|---------------|
| 1 | #46 | feat: repo-root detection utility | Muse CLI; touches `maestro/muse_cli/repo.py`, `errors.py`. Standalone foundation. |
| 2 | #39 | feat: Muse Hub FastAPI backend | Muse Hub; touches `maestro/api/routes/musehub/`, services, models. Standalone API layer. |

No dependency between these issues. Different areas (CLI vs Hub API). They can run in parallel without conflicts.

---

## After Agents Complete

1. Click **Apply** on each agent's worktree to merge changes into your branch, or review each PR separately.
2. If applying both: Cursor may prompt for merge vs. full overwrite; choose based on which changes you want to keep.
3. Run `git worktree list` to see Cursor-created worktrees.

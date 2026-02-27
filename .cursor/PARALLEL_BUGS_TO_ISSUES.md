# Parallel Agent Kickoff — Bug Reports → GitHub Issues (N agents)

Coordination template for **CREATE_ISSUES_PROMPT.md**.
Each agent claims a task number and converts its assigned batch of bug reports into well-structured GitHub issues.

---

## Environment (read before doing anything else)

**You are running inside a Cursor worktree.** Your working directory is NOT the main repo.

| Item | Value |
|------|-------|
| Your worktree root | `/Users/gabriel/.cursor/worktrees/maestro/<id>/` (wherever you are) |
| Main repo | `/Users/gabriel/dev/tellurstori/maestro` |
| GitHub CLI | `gh` — already authenticated |

**No Docker commands needed for this workflow.** Issues are created via `gh issue create` — no code changes, no mypy, no tests.

---

## Self-assignment

Read `.agent-id` in your working directory. If it doesn't exist (the setup script may not have run in worktrees), determine your task number from your worktree directory name:
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
1. Your worktree is at your current working directory (run `pwd` to confirm).
2. No Docker needed for this workflow — you only need gh CLI to create issues.
3. Check gh is authenticated: gh auth status

SELF-ASSIGNMENT:
Read .agent-id in your working directory. If missing, use your worktree folder name:
- first alphabetically (e.g. eas) → 1
- second (e.g. jzx) → 2
- third (e.g. wfk) → 3
Write it: echo "N" > .agent-id

TASKS (execute ONLY the task matching your number):

**Agent 1 — Bugs:**
BUG_1A: <paste bug report>
BUG_1B: <paste bug report>
BUG_1C: <paste bug report>
(one issue per bug)

**Agent 2 — Bugs:**
BUG_2A: <paste bug report>
BUG_2B: <paste bug report>
BUG_2C: <paste bug report>
(one issue per bug)

**Agent 3 — Bugs:**
BUG_3A: <paste bug report>
BUG_3B: <paste bug report>
BUG_3C: <paste bug report>
(one issue per bug)

WORKFLOW:
Read and follow .github/CREATE_ISSUES_PROMPT.md exactly.
For each bug in your batch:
1. Analyze the bug using the domain context in CREATE_ISSUES_PROMPT.md.
2. Draft the full issue body (all sections: description, user journey, location, fix shape, tests, docs, labels).
3. Create the issue on GitHub:
   gh issue create \
     --title "Fix: <short description>" \
     --body "$(cat <<'EOF'
<full issue body>
EOF
)" \
     --label "bug,<other-labels>"
4. Report the created issue URL.

Report: agent ID, list of created issue URLs with titles.
```

---

## Before launching

1. Paste the bug reports into the prompt above, split evenly between agents.
2. Issues will be created in `cgcardona/maestro` (the default `gh` remote).
3. No branch or Docker setup needed — pure issue creation.

---

## After agents complete

- Review the created issues on GitHub for accuracy.
- Add any missing `blocks #N` / `related to #N` cross-references manually if needed.
- Issues are immediately available for the **PARALLEL_ISSUE_TO_PR.md** workflow.

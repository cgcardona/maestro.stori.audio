# Parallel Agent Kickoff — Bug Reports → GitHub Issues

Each agent gets its own ephemeral worktree. Worktrees are created at kickoff,
named by batch number, and **deleted by the agent when its job is done**.
No branch, no Docker — pure `gh issue create`.

---

## Architecture

```
Kickoff (coordinator)
  └─ for each batch of bugs:
       git worktree add .../batch-<N>  dev   ← fresh worktree, named by batch
       write .agent-task into it             ← bug assignments, no guessing
       launch agent in that directory

Agent (per worktree)
  └─ cat .agent-task                         ← reads its bug batch
  └─ for each bug: draft → gh issue create
  └─ git worktree remove --force <path>      ← self-destructs when done
  └─ git -C <main-repo> worktree prune       ← cleans up the ref
```

Worktrees are **not** kept around between cycles. If an agent crashes before
cleanup, run `git worktree prune` from the main repo.

---

## Setup — run this before launching agents

Run from anywhere inside the main repo. Paths are derived automatically.
**Fill in the bug descriptions in each `.agent-task` before launching agents.**

```bash
REPO=$(git rev-parse --show-toplevel)
PRTREES="$HOME/.cursor/worktrees/$(basename "$REPO")"
cd "$REPO"

# Number of agents (one per batch)
NUM_AGENTS=3

for i in $(seq 1 $NUM_AGENTS); do
  WT="$PRTREES/batch-$i"
  git worktree add "$WT" dev
  cat > "$WT/.agent-task" <<EOF
WORKFLOW=bugs-to-issues
BATCH_NUM=$i

BUGS:
# Paste bug descriptions for batch $i below, one per section.
# Each bug becomes one GitHub issue.

## Bug 1
<description>

## Bug 2
<description>
EOF
  echo "✅ worktree batch-$i ready — fill in .agent-task"
done

git worktree list
```

After filling in each `.agent-task`, open one Cursor composer window per
worktree, each rooted in its `batch-<N>` directory, and paste the Kickoff
Prompt below.

---

## Environment (agents read this first)

**You are running inside a Cursor worktree.** Your working directory is NOT the main repo.

```bash
# Derive main repo path if needed
REPO=$(git worktree list | head -1 | awk '{print $1}')
```

| Item | Value |
|------|-------|
| Your worktree root | current directory (contains `.agent-task`) |
| Main repo | first entry of `git worktree list` |
| GitHub CLI | `gh` — already authenticated |

**No Docker needed.** Issues are created via `gh issue create` — no code
changes, no mypy, no tests.

---

## Kickoff Prompt

```
PARALLEL AGENT COORDINATION — BUGS TO ISSUES

STEP 0 — READ YOUR TASK:
  cat .agent-task
  This file contains your batch of bug reports to convert into GitHub issues.

STEP 1 — VERIFY GH AUTH:
  gh auth status

STEP 2 — CREATE ISSUES:
  Read and follow every step in .github/CREATE_ISSUES_PROMPT.md exactly.
  For each bug in your batch:
  1. Analyze the bug using the domain context in CREATE_ISSUES_PROMPT.md
  2. Check for an existing issue BEFORE creating a new one (idempotency gate):
       gh issue list --search "Fix: <short description>" --state all --json number,title,url
     If a matching open or closed issue already exists → skip creation, record the existing URL.
  3. Draft the full issue body (description, user journey, location, fix shape, tests, docs, labels)
  4. Create the issue (only if step 2 found nothing):
       gh issue create \
         --title "Fix: <short description>" \
         --body "$(cat <<'EOF'
<full issue body>
EOF
)" \
         --label "bug,<other-labels>"
  5. Record the created issue URL.
     ⚠️  If gh issue create fails twice for the same bug, skip it and report the failure —
     do NOT loop endlessly. Change strategy or escalate.

STEP 3 — SELF-DESTRUCT (always run this when done):
  REPO=$(git worktree list | head -1 | awk '{print $1}')
  WORKTREE=$(pwd)
  cd "$REPO"
  git worktree remove --force "$WORKTREE"
  git worktree prune

Report: batch number, explicit list of created issue URLs with titles.
⚠️  An empty URL list is a failure — "Done" is not an acceptable report without artifact proof.
```

---

## Before launching

1. Create worktrees with the setup script above.
2. Fill in `.agent-task` bug descriptions in each worktree.
3. Confirm `gh` is authenticated: `gh auth status`
4. Confirm issues will land in the right repo: `gh repo view`

---

## After agents complete

- Review created issues on GitHub for accuracy and completeness.
- Add any `blocks #N` / `related to #N` cross-references if needed.
- Verify no stale worktrees remain: `git worktree list` — should show only the main repo.
  If any linger (agent crashed before cleanup):
  ```bash
  git -C "$(git rev-parse --show-toplevel)" worktree prune
  ```
- Issues are immediately available for the **PARALLEL_ISSUE_TO_PR.md** workflow.

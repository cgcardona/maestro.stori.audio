# Parallel Agent Kickoff — Bug Reports → GitHub Issues

> ## YOU ARE THE COORDINATOR
>
> If you are an AI agent reading this document, your role is **coordinator only**.
>
> **Your job — the full list, nothing more:**
> 1. Fill in the bug descriptions in each `.agent-task` file (or confirm the user has done so).
> 2. Run the Setup script below to create one worktree per batch.
> 3. Launch one sub-agent per worktree by pasting the Kickoff Prompt (found at the bottom of this document) into a separate Cursor composer window rooted in that worktree.
> 4. Report back once all sub-agents have been launched.
>
> **You do NOT:**
> - Draft or create any GitHub issues yourself.
> - Read bug reports and analyze them yourself.
> - Run `gh issue create` yourself.
>
> The **Kickoff Prompt** at the bottom of this document is for the sub-agents, not for you.
> Copy it verbatim into each sub-agent's window. Do not follow it yourself.

---

Each sub-agent gets its own ephemeral worktree. Worktrees are created at kickoff,
named by batch number, and **deleted by the sub-agent when its job is done**.
No branch, no Docker — pure `gh issue create`.

---

## Architecture

```
Kickoff (coordinator)
  └─ for each batch of bugs:
       DEV_SHA=$(git rev-parse dev)
       git worktree add --detach .../batch-<N> "$DEV_SHA"  ← detached HEAD at dev tip
       write .agent-task into it                            ← bug assignments, no guessing
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

> **GitHub repo slug:** Always `cgcardona/maestro`. The local path
> (`/Users/gabriel/dev/tellurstori/maestro`) is misleading — `tellurstori` is
> NOT the GitHub org. Never derive the slug from `basename` or `pwd`.

```bash
REPO=$(git rev-parse --show-toplevel)
PRTREES="$HOME/.cursor/worktrees/$(basename "$REPO")"
cd "$REPO"

# Number of agents (one per batch)
NUM_AGENTS=3

DEV_SHA=$(git rev-parse dev)

for i in $(seq 1 $NUM_AGENTS); do
  WT="$PRTREES/batch-$i"
  if [ -d "$WT" ]; then
    echo "⚠️  worktree batch-$i already exists, skipping"
    continue
  fi
  git worktree add --detach "$WT" "$DEV_SHA"
  cat > "$WT/.agent-task" <<EOF
WORKFLOW=bugs-to-issues
BATCH_NUM=$i
ROLE=coordinator

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
REPO=$(git worktree list | head -1 | awk '{print $1}')   # local filesystem path only

# GitHub repo slug — HARDCODED. NEVER derive from local path or directory name.
# The local path is /Users/gabriel/dev/tellurstori/maestro — "tellurstori" is NOT the GitHub org.
export GH_REPO=cgcardona/maestro
```

| Item | Value |
|------|-------|
| Your worktree root | current directory (contains `.agent-task`) |
| Main repo (local path) | first entry of `git worktree list` |
| GitHub repo slug | `cgcardona/maestro` — always hardcoded, never derived |
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

STEP 0.5 — LOAD YOUR ROLE:
  ROLE=$(grep '^ROLE=' .agent-task | cut -d= -f2)
  REPO=$(git worktree list | head -1 | awk '{print $1}')
  ROLE_FILE="$REPO/.cursor/roles/${ROLE}.md"
  if [ -f "$ROLE_FILE" ]; then
    cat "$ROLE_FILE"
    echo "✅ Operating as role: $ROLE"
  else
    echo "⚠️  No role file found for '$ROLE' — proceeding without role context."
  fi
  # The decision hierarchy, quality bar, and failure modes in that file govern
  # all your choices from this point forward.

STEP 1 — SET GITHUB REPO AND VERIFY AUTH:
  # GitHub repo slug — ALWAYS hardcoded. NEVER derive from directory name or local path.
  # The local path contains "tellurstori" but the GitHub org is "cgcardona".
  export GH_REPO=cgcardona/maestro
  # All gh commands pick this up automatically. You may also pass --repo "$GH_REPO" explicitly.

  gh auth status

STEP 2 — CREATE ISSUES:
  Read and follow every step in .github/CREATE_ISSUES_PROMPT.md exactly.
  For each bug in your batch:
  1. Analyze the bug using the domain context in CREATE_ISSUES_PROMPT.md
  2. Check for an existing issue BEFORE creating a new one (idempotency gate):
       gh issue list --search "Fix: <short description>" --state all --json number,title,url
     If a matching open or closed issue already exists → skip creation, record the existing URL.
  3. Draft the full issue body (description, user journey, location, fix shape, tests, docs, labels)
  4. Create the issue (only if step 2 found nothing) using the TWO-STEP PATTERN:

     ── LABEL REFERENCE (only use labels from this list) ──────────────────────
     │ bug              documentation     duplicate         enhancement        │
     │ good first issue help wanted       invalid           question           │
     │ wontfix          multimodal        performance       ai-pipeline        │
     │ muse             muse-cli          muse-hub          storpheus          │
     │ maestro-integration  mypy          cli               testing            │
     │ weekend-mvp      muse-music-extensions                                  │
     │                                                                         │
     │ ⚠️  Never invent labels (e.g. "tech-debt", "mcp", "budget",            │
     │    "security" do NOT exist). Using a missing label causes               │
     │    gh issue create to fail entirely.                                    │
     └─────────────────────────────────────────────────────────────────────────

     ── TWO-STEP PATTERN ──────────────────────────────────────────────────────
     │ Step 1: create without --label (never fails due to labels)             │
     │ Step 2: apply labels with gh issue edit || true (non-fatal per label)  │
     └─────────────────────────────────────────────────────────────────────────

       ISSUE_URL=$(gh issue create \
         --title "Fix: <short description>" \
         --body "$(cat <<'EOF'
<full issue body>
EOF
)")
       # Apply each label on its own line from the LABEL REFERENCE above.
       gh issue edit "$ISSUE_URL" --add-label "bug" 2>/dev/null || true
       # gh issue edit "$ISSUE_URL" --add-label "<second-label>" 2>/dev/null || true

  5. Record the created issue URL ($ISSUE_URL).
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

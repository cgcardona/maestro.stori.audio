# Parallel Agent Kickoff — Bug Reports → GitHub Issues

> ## YOU ARE THE COORDINATOR
>
> If you are an AI agent reading this document, your role is **coordinator only**.
>
> **Your job — the full list, nothing more:**
> 1. Run the **Label Pre-flight** script to create required labels and delete stale ones.
> 2. Fill in the bug descriptions in each `.agent-task` file (or confirm the user has done so), including the label fields.
> 3. Run the **Setup** script to create one worktree per batch.
> 4. Launch one sub-agent per worktree using the Task tool (or Cursor composer window). Each sub-agent reads its own `.agent-task` — you do not need to pass context manually.
> 5. Report back once all sub-agents have been launched.
>
> **You do NOT:**
> - Draft or create any GitHub issues yourself.
> - Read bug reports and analyze them yourself.
> - Run `gh issue create` yourself.
> - Apply labels yourself.
>
> The **Kickoff Prompt** at the bottom of this document is for the sub-agents, not for you.
> Do not follow it yourself.

---

## Why `.agent-task` files unlock more than 4 parallel agents

When a coordinator manages parallel work by holding all task details in its own
context window, it is limited to ~4 concurrent agents before context overhead
becomes a bottleneck.

**`.agent-task` files break this limit** by externalising all task state into
the filesystem. The coordinator writes every task file upfront — one per batch
or per issue — and then launches agents without needing to hold any task content
itself. Each agent is fully self-sufficient: it reads its `.agent-task` file on
startup and knows exactly what to do.

This also enables **nested orchestration**: a "sub-coordinator" agent can read
its task file, discover it has sub-tasks to delegate, write sub-task files, and
launch its own leaf agents — creating a tree of unlimited depth.

```
Coordinator
  ├── writes batch-1/.agent-task  (SPAWN_SUB_AGENTS=true, 4 issues)
  ├── writes batch-2/.agent-task  (SPAWN_SUB_AGENTS=true, 4 issues)
  ├── writes batch-3/.agent-task  (SPAWN_SUB_AGENTS=true, 4 issues)
  └── launches 3 sub-coordinators simultaneously

Sub-coordinator (batch-1)
  ├── reads batch-1/.agent-task
  ├── writes issue-A/.agent-task
  ├── writes issue-B/.agent-task
  ├── writes issue-C/.agent-task
  ├── writes issue-D/.agent-task
  └── launches 4 leaf agents

Leaf agent (issue-A)
  └── reads issue-A/.agent-task → creates issue → self-destructs
```

With 3 sub-coordinators × 4 leaf agents = **12 agents from a single coordinator call**.
Scale by adding more batches, not by holding more context.

---

## Agent Task File Reference

Everything an agent needs to know goes in its `.agent-task` file.
The file is plain text; fields are `KEY=value` lines followed by free-form content.
Agents parse what they need and ignore fields they don't recognise.

### All supported fields

```
# ── Identity ──────────────────────────────────────────────────────────────────
WORKFLOW=bugs-to-issues          # bugs-to-issues | issue-to-pr | pr-review
BATCH_NUM=3                      # batch number (used in worktree name)

# ── GitHub Labels ─────────────────────────────────────────────────────────────
# Labels MUST exist on GitHub before agents try to apply them.
# Run the Label Pre-flight script (below) before creating worktrees.
PHASE_LABEL=phase-2/core-api           # phase label (parent category)
BATCH_LABEL=batch-03                   # batch label (child category)
LABELS_TO_APPLY=enhancement,muse-hub,phase-2/core-api,batch-03
# ↑ Comma-separated. Each applied individually (|| true) so one failure
#   never blocks the others. Add domain labels here too (muse-cli, etc.).

# ── File Ownership (conflict avoidance) ───────────────────────────────────────
# Declare which files/dirs this batch exclusively owns.
# Used by the coordinator to verify no two batches share a file.
FILE_OWNERSHIP=maestro/api/routes/musehub/milestones.py,maestro/db/musehub_milestone_models.py

# ── Dependencies ──────────────────────────────────────────────────────────────
# Do not implement until these batch numbers or issue numbers are merged.
DEPENDS_ON=batch-01,batch-02

# ── Sub-agent Orchestration ───────────────────────────────────────────────────
# Set to true to make this agent act as a sub-coordinator that spawns
# its own leaf agents rather than doing the work directly.
SPAWN_SUB_AGENTS=false
# If SPAWN_SUB_AGENTS=true, list the sub-task worktree paths to create:
# SUB_TASK_PATHS=/path/to/sub1,/path/to/sub2

# ── Output Contract ───────────────────────────────────────────────────────────
# What the agent must produce and report back.
# For bugs-to-issues: a list of created issue URLs.
REQUIRED_OUTPUT=issue_urls

# ── Escalation ────────────────────────────────────────────────────────────────
# If the agent is blocked (e.g. gh auth fails, API errors), what to do.
# stop = self-destruct and report. retry = try once more. escalate = notify coordinator.
ON_BLOCK=stop

# ── Environment hints ─────────────────────────────────────────────────────────
GH_REPO=cgcardona/maestro        # always hardcoded, never derived from local path
```

### Free-form content (after the key=value header)

Below the key=value block, the file contains the actual task content.
For `bugs-to-issues`, this is the list of bug descriptions — one `## Issue N` section per issue.

### Nested sub-coordinator example

If `SPAWN_SUB_AGENTS=true`, the agent's task file looks like this:

```
WORKFLOW=bugs-to-issues
BATCH_NUM=1
SPAWN_SUB_AGENTS=true
PHASE_LABEL=phase-1/db-schema
BATCH_LABEL=batch-01
LABELS_TO_APPLY=enhancement,muse-hub,phase-1/db-schema,batch-01

SUB_TASKS:
## Sub-task A
ISSUE_TITLE=[DB] Add musehub_milestones table
ISSUE_BODY=...full description...

## Sub-task B
ISSUE_TITLE=[DB] Add musehub_labels table
ISSUE_BODY=...full description...
```

The sub-coordinator agent:
1. Reads the task file and discovers `SPAWN_SUB_AGENTS=true`
2. Creates sub-worktrees (e.g. `batch-1a`, `batch-1b`)
3. Writes a `.agent-task` file into each with `SPAWN_SUB_AGENTS=false`
4. Launches one leaf agent per sub-worktree
5. Collects leaf agent reports and self-destructs

---

## Architecture

```
Coordinator
  └─ Label Pre-flight (create/delete labels on GitHub)
  └─ for each batch:
       DEV_SHA=$(git rev-parse dev)
       git worktree add --detach .../batch-<N> "$DEV_SHA"
       write .agent-task into it   ← ALL context the agent needs, incl. labels
       launch agent in that directory

Agent (per worktree)
  └─ cat .agent-task               ← reads LABELS_TO_APPLY and all other fields
  └─ for each bug: draft → gh issue create (no --label)
  └─ for each label in LABELS_TO_APPLY: gh issue edit --add-label || true
  └─ git worktree remove --force   ← self-destructs when done
  └─ git worktree prune
```

---

## Step 0 — Label Pre-flight (run ONCE before creating worktrees)

Run this before the Setup script. It ensures every label in every
`LABELS_TO_APPLY` field exists on GitHub — and deletes any stale labels from
previous naming schemes that would pollute autocomplete.

```bash
export GH_REPO=cgcardona/maestro

# ── 1. Identify stale labels to delete ────────────────────────────────────────
# List all phase/batch labels currently on GitHub so you can spot obsolete ones.
echo "=== Current phase/batch labels ==="
gh label list --repo "$GH_REPO" --limit 100 | grep -E "^(phase|batch)" | sort

# Delete stale labels (edit this list to match what's actually stale):
# for label in phase-1 phase-2 batch-1 batch-2; do
#   gh label delete "$label" --repo "$GH_REPO" --yes 2>/dev/null || true
# done

# ── 2. Create/update phase labels ─────────────────────────────────────────────
# Add any phase labels needed for this run. gh label create fails silently if
# the label already exists with the correct colour — use --force to update colour/desc.
declare -A PHASE_LABELS=(
  ["phase-1/db-schema"]="0052cc|Phase 1 · DB Schema: new Alembic migrations + ORM models"
  ["phase-2/core-api"]="0075ca|Phase 2 · Core API: brand-new FastAPI route files"
  ["phase-3/api-extensions"]="0e8a16|Phase 3 · API Extensions: new endpoints on existing route files"
  ["phase-4/new-ui-pages"]="006b75|Phase 4 · New UI Pages: brand-new page route files"
  ["phase-5/ui-enhancements"]="5319e7|Phase 5 · UI Enhancements: improvements to existing pages"
  ["phase-6/seed-data"]="b60205|Phase 6 · Seed Data: 10x seed script, CC MIDI, narratives"
  ["phase-7/machine-access"]="e4e669|Phase 7 · Machine Access: JSON alternates, oEmbed, JSON-LD, RSS"
)
for label in "${!PHASE_LABELS[@]}"; do
  IFS='|' read -r color desc <<< "${PHASE_LABELS[$label]}"
  gh label create "$label" --repo "$GH_REPO" --color "$color" --description "$desc" 2>/dev/null \
    || gh label edit "$label" --repo "$GH_REPO" --color "$color" --description "$desc" 2>/dev/null \
    || true
  echo "✅ phase label: $label"
done

# ── 3. Create/update status labels (pipeline state — used by conductor) ───────
declare -A STATUS_LABELS=(
  ["status/ready"]="0e8a16|Issue is open and ready for an ISSUE_TO_PR agent"
  ["status/in-progress"]="e4e669|ISSUE_TO_PR agent currently working on this issue"
  ["status/pr-open"]="d93f0b|PR created, awaiting PR_REVIEW agent"
  ["status/merged"]="6f42c1|PR merged and issue closed"
  ["conductor-reminder"]="e4e669|Open: pipeline incomplete — re-run PARALLEL_CONDUCTOR.md"
)
for label in "${!STATUS_LABELS[@]}"; do
  IFS='|' read -r color desc <<< "${STATUS_LABELS[$label]}"
  gh label create "$label" --repo "$GH_REPO" --color "$color" --description "$desc" 2>/dev/null \
    || gh label edit "$label" --repo "$GH_REPO" --color "$color" --description "$desc" 2>/dev/null \
    || true
  echo "✅ status label: $label"
done

# ── 4. Create/update batch labels ─────────────────────────────────────────────
for i in $(seq 1 15); do
  N=$(printf "%02d" $i)
  gh label create "batch-$N" --repo "$GH_REPO" \
    --color "f4a261" \
    --description "Batch $N · 4 parallel issues guaranteed no file conflicts" \
    2>/dev/null || true
done
echo "✅ batch labels 01-15 ready"

# ── 4. Verify all required labels exist ───────────────────────────────────────
echo "=== All phase/batch labels after pre-flight ==="
gh label list --repo "$GH_REPO" --limit 100 | grep -E "^(phase|batch)" | sort
```

---

## Setup — run this after the Label Pre-flight

Run from anywhere inside the main repo. Paths are derived automatically.
**Fill in the bug descriptions in each `.agent-task` before launching agents.**

> **GitHub repo slug:** Always `cgcardona/maestro`. The local path
> (`/Users/gabriel/dev/tellurstori/maestro`) is misleading — `tellurstori` is
> NOT the GitHub org. Never derive the slug from `basename` or `pwd`.

```bash
REPO=$(git rev-parse --show-toplevel)
PRTREES="$HOME/.cursor/worktrees/$(basename "$REPO")"
mkdir -p "$PRTREES"
cd "$REPO"

GH_REPO=cgcardona/maestro
NUM_BATCHES=3   # one worktree per batch

DEV_SHA=$(git rev-parse dev)

for i in $(seq 1 $NUM_BATCHES); do
  WT="$PRTREES/batch-$i"
  if [ -d "$WT" ]; then
    echo "⚠️  worktree batch-$i already exists, skipping"
    continue
  fi
  git worktree add --detach "$WT" "$DEV_SHA"

  # ── Write the task file ───────────────────────────────────────────────────
  # Edit: PHASE_LABEL, BATCH_LABEL, LABELS_TO_APPLY, FILE_OWNERSHIP per batch.
  # Then add the bug descriptions under BUGS:.
  cat > "$WT/.agent-task" << TASKEOF
WORKFLOW=bugs-to-issues
BATCH_NUM=$i
GH_REPO=$GH_REPO

PHASE_LABEL=phase-X/name-here
BATCH_LABEL=batch-$(printf "%02d" $i)
LABELS_TO_APPLY=enhancement,muse-hub,phase-X/name-here,batch-$(printf "%02d" $i)

FILE_OWNERSHIP=
DEPENDS_ON=
SPAWN_SUB_AGENTS=false
ATTEMPT_N=0
REQUIRED_OUTPUT=issue_urls
ON_BLOCK=stop

BUGS:
# Paste bug descriptions for batch $i below, one per section.
# Each ## Issue N block becomes one GitHub issue.

## Issue 1
<title>
<description>

## Issue 2
<title>
<description>
TASKEOF

  echo "✅ worktree batch-$i ready — fill in PHASE_LABEL, BATCH_LABEL, LABELS_TO_APPLY, and BUGS"
done

git worktree list
```

After filling in each `.agent-task`, launch one agent per worktree using the
Task tool (recommended — allows more than 4 simultaneous agents) or by pasting
the Kickoff Prompt into a Cursor composer window rooted in that worktree.

**Using the Task tool (preferred):** you can launch all batches simultaneously
in a single message because each agent reads its own task file independently:

```python
# Launch all 3 batch agents in one message — they run fully in parallel
Task(worktree="/path/to/batch-1", prompt=KICKOFF_PROMPT)
Task(worktree="/path/to/batch-2", prompt=KICKOFF_PROMPT)
Task(worktree="/path/to/batch-3", prompt=KICKOFF_PROMPT)
```

---

## Environment (agents read this first)

**You are running inside a Cursor worktree.** Your working directory is NOT the main repo.

```bash
# Derive main repo path if needed
REPO=$(git worktree list | head -1 | awk '{print $1}')   # local filesystem path only

# GitHub repo slug — read from .agent-task GH_REPO field, or use hardcoded default.
# NEVER derive from local path or directory name.
export GH_REPO=$(grep "^GH_REPO=" .agent-task | cut -d= -f2)
export GH_REPO=${GH_REPO:-cgcardona/maestro}   # fallback
```

| Item | Value |
|------|-------|
| Your worktree root | current directory (contains `.agent-task`) |
| Main repo (local path) | first entry of `git worktree list` |
| GitHub repo slug | read from `.agent-task` GH_REPO field — always `cgcardona/maestro` |
| GitHub CLI | `gh` — already authenticated |

**No Docker needed.** Issues are created via `gh issue create` — no code
changes, no mypy, no tests.

---

## Kickoff Prompt

```
PARALLEL AGENT COORDINATION — BUGS TO ISSUES

STEP 0 — READ YOUR TASK FILE:
  cat .agent-task

  Parse these fields from the header (KEY=value lines):
    WORKFLOW        — must be "bugs-to-issues"
    BATCH_NUM       — your batch number
    GH_REPO         — GitHub repo slug (hardcoded: cgcardona/maestro)
    PHASE_LABEL     — the phase label to apply to every issue (e.g. phase-2/core-api)
    BATCH_LABEL     — the batch label (e.g. batch-03)
    LABELS_TO_APPLY — comma-separated list of ALL labels to apply to every issue
    FILE_OWNERSHIP  — which files this batch owns (for your awareness, not action needed)
    DEPENDS_ON      — batch/issue numbers that must be merged before you run
    SPAWN_SUB_AGENTS — if true, follow the sub-coordinator path instead of this path

  Export for use in all subsequent commands:
    export GH_REPO=$(grep "^GH_REPO=" .agent-task | cut -d= -f2)
    export GH_REPO=${GH_REPO:-cgcardona/maestro}

    PHASE_LABEL=$(grep "^PHASE_LABEL=" .agent-task | cut -d= -f2)
    BATCH_LABEL=$(grep "^BATCH_LABEL=" .agent-task | cut -d= -f2)
    LABELS_TO_APPLY=$(grep "^LABELS_TO_APPLY=" .agent-task | cut -d= -f2)
    ATTEMPT_N=$(grep "^ATTEMPT_N=" .agent-task | cut -d= -f2)

  ⚠️  ANTI-LOOP GUARD: if ATTEMPT_N > 2 → STOP immediately.
    You have retried this task 3+ times. Continuing is almost certainly wrong.
    Self-destruct and escalate: report the exact failure from your last attempt
    so a human can diagnose the root cause. Never loop blindly.

  ⚠️  RETRY-WITHOUT-STRATEGY-MUTATION: if a command fails twice in a row with
    the same error, you MUST change strategy — not retry with minor parameter tweaks.
    Two identical failures = the approach is wrong. Stop. Redesign. Or escalate.

  If SPAWN_SUB_AGENTS=true → follow the sub-coordinator path:
    1. For each sub-task in the file, create a sub-worktree with its own .agent-task
    2. Launch one leaf agent per sub-worktree (Task tool or Cursor composer)
    3. Collect leaf reports and self-destruct
    (This enables nested orchestration — see Agent Task File Reference in the main doc)

STEP 1 — VERIFY AUTH AND LABELS EXIST:
  gh auth status
  # Verify every label in LABELS_TO_APPLY exists on GitHub:
  IFS=',' read -ra LABELS <<< "$LABELS_TO_APPLY"
  for label in "${LABELS[@]}"; do
    FOUND=$(gh label list --repo "$GH_REPO" --search "$label" --json name --jq '.[].name' 2>/dev/null)
    if [ -z "$FOUND" ]; then
      echo "❌ Label '$label' does not exist on GitHub. Run the Label Pre-flight script."
      echo "   Continuing — label application will use || true so this is non-fatal."
    fi
  done

STEP 2 — CREATE ISSUES:
  For each ## Issue N section in the BUGS: block of .agent-task:

  1. Check for an existing issue first (idempotency gate):
       gh issue list --repo "$GH_REPO" --search "<title>" --state all --json number,title,url | head -3
     If a matching issue already exists → skip creation, record the existing URL.

  2. Create the issue WITHOUT --label (the two-step pattern prevents label failures
     from blocking issue creation):

     ISSUE_URL=$(gh issue create \
       --repo "$GH_REPO" \
       --title "<title from task file>" \
       --body "$(cat <<'EOF'
<full body from task file — include Phase, Batch, File Ownership, Depends On,
 and all technical spec details>
EOF
)")

  3. Apply EVERY label from LABELS_TO_APPLY individually (|| true = non-fatal):
     IFS=',' read -ra LABELS <<< "$LABELS_TO_APPLY"
     for label in "${LABELS[@]}"; do
       gh issue edit "$ISSUE_URL" --repo "$GH_REPO" --add-label "$label" 2>/dev/null || true
     done

     # Also apply any domain-specific labels listed in the issue section itself:
     # (e.g. muse-cli for commands that map to CLI commands)
     # gh issue edit "$ISSUE_URL" --repo "$GH_REPO" --add-label "muse-cli" 2>/dev/null || true

  4. Record the created issue URL.
     ⚠️  If gh issue create fails twice for the same issue, skip it and report
     the failure — do NOT loop endlessly.

  ── VALID LABEL REFERENCE ────────────────────────────────────────────────────
  │ Standard labels (always exist):                                            │
  │   bug             documentation   duplicate        enhancement             │
  │   good first issue  help wanted   invalid          question                │
  │   wontfix         multimodal      performance      ai-pipeline             │
  │   muse            muse-cli        muse-hub         storpheus               │
  │   maestro-integration  mypy       cli              testing                 │
  │   weekend-mvp     muse-music-extensions                                    │
  │                                                                            │
  │ Phase labels (created by Label Pre-flight):                                │
  │   phase-1/db-schema      phase-2/core-api      phase-3/api-extensions     │
  │   phase-4/new-ui-pages   phase-5/ui-enhancements  phase-6/seed-data       │
  │   phase-7/machine-access                                                   │
  │                                                                            │
  │ Batch labels (created by Label Pre-flight):                                │
  │   batch-01  batch-02  batch-03  batch-04  batch-05  batch-06  batch-07    │
  │   batch-08  batch-09  batch-10  batch-11  batch-12  batch-13  batch-14    │
  │   batch-15                                                                 │
  │                                                                            │
  │ ⚠️  Never invent labels. A missing label causes gh issue edit to fail     │
  │    (non-fatal with || true), but leaves the issue mislabeled.             │
  └────────────────────────────────────────────────────────────────────────────

STEP 3 — SELF-DESTRUCT (always run this when done):
  REPO=$(git worktree list | head -1 | awk '{print $1}')
  WORKTREE=$(pwd)
  cd "$REPO"
  git worktree remove --force "$WORKTREE"
  git worktree prune

Report: batch number, PHASE_LABEL, BATCH_LABEL, and the explicit list of
created issue URLs with titles. ⚠️  An empty URL list is a failure — "Done"
without artifact proof is not acceptable.
```

---

## Before launching

1. Run the **Label Pre-flight** script above.
2. Run the **Setup** script and fill in `.agent-task` files (PHASE_LABEL, BATCH_LABEL, LABELS_TO_APPLY, BUGS).
3. Confirm `gh` is authenticated: `gh auth status`
4. Confirm issues will land in the right repo: `gh repo view cgcardona/maestro`

---

## After agents complete

- Review created issues on GitHub for accuracy and label correctness.
- Verify every issue has: phase label + batch label + domain labels.
  ```bash
  gh issue list --repo cgcardona/maestro --label "batch-01" --json number,title,labels
  ```
- Add `blocks #N` / `related to #N` cross-references if needed.
- Verify no stale worktrees remain: `git worktree list` — should show only the main repo.
  If any linger (agent crashed before cleanup):
  ```bash
  git -C "$(git rev-parse --show-toplevel)" worktree prune
  ```
- Issues are immediately available for the **PARALLEL_ISSUE_TO_PR.md** workflow.
  To dispatch the next wave, select issues by batch label:
  ```bash
  gh issue list --repo cgcardona/maestro --label "batch-01" --state open --json number,title,url
  ```

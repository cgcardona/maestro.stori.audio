# Parallel Agent Kickoff — PR Review → Grade → Merge

> ## YOU ARE THE COORDINATOR
>
> If you are an AI agent reading this document, your role is **coordinator only**.
>
> **Your job — the full list, nothing more:**
> 1. Pull `dev` to confirm it is up to date.
> 2. Run the Setup script below to create one worktree per PR.
> 3. Launch one sub-agent per worktree by pasting the Kickoff Prompt (found at the bottom of this document) into a separate Cursor composer window rooted in that worktree.
> 4. Report back once all sub-agents have been launched.
>
> **You do NOT:**
> - Check out branches or review any PR yourself.
> - Run mypy or pytest yourself.
> - Merge or close any PR yourself.
> - Read PR diffs or study code yourself.
>
> The **Kickoff Prompt** at the bottom of this document is for the sub-agents, not for you.
> Copy it verbatim into each sub-agent's window. Do not follow it yourself.

---

Each sub-agent gets its own ephemeral worktree. Worktrees are created at kickoff,
named by PR number, and **deleted by the sub-agent when its job is done** — whether
the PR was merged, rejected, or left for human review. The branch lives on
GitHub regardless; the local worktree is just a working directory.

---

## Architecture

```
Kickoff (coordinator)
  └─ for each PR:
       DEV_SHA=$(git rev-parse dev)
       git worktree add --detach .../pr-<N> "$DEV_SHA"  ← detached HEAD at dev tip
       write .agent-task into it                         ← task assignment, no guessing
       launch agent in that directory

Agent (per worktree)
  └─ cat .agent-task                        ← knows exactly what to do
  └─ gh pr view <N> --json state,...        ← CHECK FIRST: merged/closed/approved?
                                               if so → stop + self-destruct
  └─ gh pr checkout <N>                     ← checks out the PR branch (only if open)
  └─ git fetch origin && git merge origin/dev  ← sync latest dev into feature branch
  └─ review → grade → pre-merge sync → merge (or reject)
  └─ git worktree remove --force <path>     ← self-destructs when done
  └─ git -C <main-repo> worktree prune      ← cleans up the ref
```

Worktrees are **not** kept around between cycles. If an agent crashes before
cleanup, run `git worktree prune` from the main repo.

---

## Setup — run this before launching agents

Run from anywhere inside the main repo. Paths are derived automatically.

> **Critical:** Worktrees use `--detach` at the dev tip SHA — never branch name
> `dev` directly. This prevents the "dev is already used by worktree" error when
> the main repo has `dev` checked out.

```bash
REPO=$(git rev-parse --show-toplevel)
PRTREES="$HOME/.cursor/worktrees/$(basename "$REPO")"
mkdir -p "$PRTREES"
cd "$REPO"

# Snapshot dev tip — all worktrees start here; agents checkout their PR branch in STEP 3
DEV_SHA=$(git rev-parse dev)

# --- define PRs ---
# Batch: #144–#151 (muse CLI commands — chord-map, form, tempo-scale, similarity, contour, diff, key, humanize)
declare -a PRS=(
  "144|feat: muse chord-map <commit> — visualize the chord progression embedded in a commit"
  "145|feat: muse form [<commit>] — analyze and display the musical form of a commit"
  "146|feat: muse tempo-scale <factor> [<commit>] — stretch or compress timing across a commit"
  "147|feat: muse similarity <commit-a> <commit-b> — compute musical similarity score between two commits"
  "148|feat: muse contour — analyze melodic contour and phrase shape"
  "149|feat: muse diff — add music-dimension diff flags (--harmonic, --rhythmic, --melodic, --structural, --dynamic)"
  "150|feat: muse key [<commit>] [--set <key>] — read or annotate the musical key of a commit"
  "151|feat: muse humanize — apply humanization to quantized MIDI"
)

# --- create worktrees + task files ---
for entry in "${PRS[@]}"; do
  NUM="${entry%%|*}"
  TITLE="${entry##*|}"
  WT="$PRTREES/pr-$NUM"
  if [ -d "$WT" ]; then
    echo "⚠️  worktree pr-$NUM already exists, skipping"
    continue
  fi
  git worktree add --detach "$WT" "$DEV_SHA"
  printf "WORKFLOW=pr-review\nPR_NUMBER=%s\nPR_TITLE=%s\nPR_URL=https://github.com/cgcardona/maestro/pull/%s\n" \
    "$NUM" "$TITLE" "$NUM" > "$WT/.agent-task"
  echo "✅ worktree pr-$NUM ready"
done

git worktree list
```

After running this, open one Cursor composer window per worktree, each rooted
in its `pr-<N>` directory, and paste the Kickoff Prompt below.

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
the container. After checking out the PR branch, your worktree's code is
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
the `dev` branch with uncommitted changes that don't belong there.

> **Alembic exception:** If the PR includes a migration, verify migration correctness by
> reading the file rather than applying it during review.

### Command policy

Consult `.cursor/AGENT_COMMAND_POLICY.md` for the full tier list. Summary:
- **Green (auto-allow):** `ls`, `git status/log/diff/fetch`, `gh pr view`, `mypy`, `pytest`, `rg`
- **Yellow (review before running):** `docker compose build`, `rm <single file>`, `git rebase`
- **Red (never):** `rm -rf`, `git push --force`, `git push origin dev`, `docker system prune`

---

## Kickoff Prompt

```
PARALLEL AGENT COORDINATION — PR REVIEW

Read .cursor/AGENT_COMMAND_POLICY.md before issuing any shell commands.
Green-tier commands run without confirmation. Yellow = check scope first.
Red = never, ask the user instead.

STEP 0 — READ YOUR TASK:
  cat .agent-task
  This file tells you your PR number, title, and URL. Substitute your actual
  PR number wherever you see <N> below.

STEP 1 — DERIVE PATHS:
  REPO=$(git worktree list | head -1 | awk '{print $1}')
  WTNAME=$(basename "$(pwd)")
  # Your worktree is live in Docker at /worktrees/$WTNAME — NO file copying needed.
  # All docker compose commands: cd "$REPO" && docker compose exec maestro <cmd>

STEP 2 — CHECK CANONICAL STATE BEFORE DOING ANY WORK:
  ⚠️  Query GitHub first. Do NOT checkout a branch, run mypy, or add a review
  comment until you have confirmed the PR is still open and unreviewed.
  This is the idempotency gate.

  # 1. What is the current state of this PR?
  gh pr view <N> --json state,mergedAt,reviews,reviewDecision,headRefName

  Decision matrix — act on the FIRST match:
  ┌────────────────────────────────────────────────────────────────────────┐
  │ state = "MERGED"   → STOP. Report already merged. Self-destruct.      │
  │ state = "CLOSED"   → STOP. Report already closed/rejected. Self-dest. │
  │ reviewDecision =   │                                                   │
  │   "APPROVED"       → STOP. Report already approved. Self-destruct.    │
  │ state = "OPEN",    │                                                   │
  │   no approval yet  → Continue to STEP 3 (full review).                │
  └────────────────────────────────────────────────────────────────────────┘

  Self-destruct when stopping early:
    WORKTREE=$(pwd)
    cd "$REPO"
    git worktree remove --force "$WORKTREE"
    git worktree prune

STEP 3 — CHECKOUT & SYNC (only if STEP 2 shows the PR is open and unreviewed):

  # 1. Checkout the PR branch into this worktree
  gh pr checkout <N>

  # 2. Fetch ALL remote refs (other agents may have merged PRs while you work)
  git fetch origin

  # 3. Merge the latest dev into this feature branch NOW
  git merge origin/dev

  ⚠️  If git merge reports "local changes would be overwritten":
    - Do NOT use git stash + git rebase. That rewrites history and is Yellow-tier.
    - Run git status to identify the unexpected modified files.
    - If they came from the checkout (gh pr checkout left dirty files), run:
        git checkout -- <file>   ← discard checkout-introduced changes, then retry merge
    - If they are your own review edits committed mid-step, commit them first:
        git add -A && git commit -m "chore: stage review edits before dev sync"
    - Then retry: git merge origin/dev

  ── If git merge reports conflicts ──────────────────────────────────────────
  │ You have full command-line authority to resolve them.                     │
  │                                                                           │
  │ 1. Inspect what conflicted:                                               │
  │      git status        ← UU = unmerged conflict, M/A = already staged   │
  │      git diff          ← shows raw conflict markers (<<<<<<< / =======)  │
  │                                                                           │
  │ 2. Identify each conflict type and apply the matching rule:               │
  │                                                                           │
  │    KNOWN-SAFE CONFLICTS (resolve mechanically — no judgment needed):     │
  │    • maestro/muse_cli/app.py                                              │
  │        Each parallel agent adds exactly one app.add_typer() line.        │
  │        Rule: KEEP ALL add_typer lines from BOTH sides. Never drop one.   │
  │        Pattern to look for:                                               │
  │          <<<<<<< HEAD                                                     │
  │          app.add_typer(foo_app, ...)                                      │
  │          =======                                                          │
  │          app.add_typer(bar_app, ...)                                      │
  │          >>>>>>> origin/dev                                               │
  │        Resolution: keep both lines, remove markers.                      │
  │                                                                           │
  │    • docs/architecture/muse_vcs.md  (most common conflict in Muse PRs)  │
  │        Diagnosis: run                                                     │
  │          grep -n "<<<<<<\|=======\|>>>>>>>" docs/architecture/muse_vcs.md│
  │        and inspect the <<<< / ==== / >>>> blocks.                        │
  │                                                                           │
  │        THREE patterns — all mechanically resolvable:                     │
  │                                                                           │
  │        A) Both sides add a NEW section (two non-empty blocks):           │
  │             <<<<<<< HEAD                                                  │
  │             ## muse foo — description                                    │
  │             ...content...                                                 │
  │             =======                                                       │
  │             ## muse bar — description                                    │
  │             ...content...                                                 │
  │             >>>>>>> origin/dev                                            │
  │           Resolution: keep BOTH sections, sort alphabetically by         │
  │           command name, remove markers.                                   │
  │                                                                           │
  │        B) One side is empty/placeholder, other has full content          │
  │           (most common — one PR didn't touch this section at all):       │
  │             <<<<<<< HEAD                                                  │
  │             (empty, or just a blank line / stub heading)                 │
  │             =======                                                       │
  │             ## muse bar — description                                    │
  │             ...full content...                                            │
  │             >>>>>>> origin/dev                                            │
  │           Resolution: keep the non-empty side entirely. Discard the      │
  │           empty side. Remove markers. Do NOT try to merge empty + full.  │
  │                                                                           │
  │        C) Both sides edited the SAME section (true conflict):            │
  │           Resolution: read both carefully. Keep the more complete /      │
  │           accurate version. Escalate to JUDGMENT CONFLICTS if unclear.   │
  │                                                                           │
  │        After resolving ALL markers in this file:                         │
  │          grep -n "<<<<<<\|=======\|>>>>>>>" docs/architecture/muse_vcs.md│
  │        Must return empty before staging.                                 │
  │                                                                           │
  │    • docs/reference/type_contracts.md                                     │
  │        Each agent registers new named types. Both registrations belong.  │
  │        Rule: keep ALL entries from BOTH sides.                            │
  │                                                                           │
  │    JUDGMENT CONFLICTS (read both sides carefully):                        │
  │    • Any file NOT in the known-safe list above.                          │
  │    • Prefer dev's version UNLESS this PR's change clearly supersedes it. │
  │    • If a conflict reveals that dev already contains this PR's feature:  │
  │      downgrade grade to C or D and explain. Do not merge redundant code. │
  │                                                                           │
  │ 3. After editing each conflicted file, verify no markers remain:          │
  │      grep -n "<<<<<<\|=======\|>>>>>>>" <file>   ← must return empty    │
  │                                                                           │
  │ 4. Stage and commit:                                                      │
  │      git add <resolved-files>                                             │
  │      git commit -m "chore: resolve merge conflicts with origin/dev"      │
  │                                                                           │
  │ 5. After resolving: re-run mypy AND tests before continuing.             │
  │    Conflicts resolved incorrectly surface as type errors or test failures.│
  │                                                                           │
  │ 6. Advanced diagnostic tools:                                             │
  │      git log --oneline origin/dev...HEAD  ← commits this PR adds         │
  │      git diff origin/dev...HEAD           ← full delta vs dev            │
  │      git log --oneline --graph --all      ← full branch picture          │
  │      git show origin/dev:path/to/file     ← see dev's version of a file │
  └───────────────────────────────────────────────────────────────────────────

STEP 4 — REGRESSION CHECK (before review):
  Check whether any commits that landed on dev since this branch diverged
  overlap with files this PR modifies. If overlap exists, run the full test
  suite — not just the PR-specific tests — to confirm no regressions.

  # What did dev gain since this branch diverged?
  git log --oneline HEAD..origin/dev

  # Do any of those commits touch the same files?
  git diff HEAD..origin/dev --name-only

  # If overlap found, run full suite:
  cd "$REPO" && docker compose exec maestro sh -c \
    "PYTHONPATH=/worktrees/$WTNAME pytest /worktrees/$WTNAME/tests/ -v --timeout=60"

STEP 5 — REVIEW:
  Read and follow every step in .github/PR_REVIEW_PROMPT.md exactly.
  1. Context — read PR description, referenced issue, commits, files changed
  2. Deep review — work through all applicable checklist sections (3a–3j)

  TYPE SYSTEM — automatic C grade if any of these are present:
    - cast() at a call site (fix the callee)
    - Any in a return type, parameter, or TypedDict field
    - object as a type annotation
    - dict[str, Any], list[dict], or bare tuples crossing module boundaries
      (must be wrapped in a named entity: <Domain><Concept>Result)
    - # type: ignore without an inline comment naming the 3rd-party issue
    - See docs/reference/type_contracts.md for the canonical entity inventory

  DOCS — automatic C grade if any of these are missing:
    - Docstrings on every new public module, class, and function
    - New muse <cmd> section in docs/architecture/muse_vcs.md
      (must include: purpose, flags table, output example, result type, agent use case)
    - New result types registered in docs/reference/type_contracts.md
    - Docs in the same commit as code (not a follow-up PR)

  3. Add/fix tests if weak or missing
  4. Run mypy first, then TARGETED tests (Docker-native, $REPO and $WTNAME from STEP 1).
     ⚠️  TARGETED TESTS ONLY — run only the test files relevant to this PR's changes.
         Do NOT run the full suite. Full suite = developer/CI responsibility only.
     ⚠️  Never pipe mypy/pytest through grep/head/tail — full output, exit code is authoritative.
  5. Broken tests from other PRs:
     If you find a failing test NOT caused by this PR, fix it anyway. Commit the fix
     with message: "fix: repair broken test <name> (pre-existing failure from dev)"
     Note it in your report. Do not leave it for the next agent to discover.
  6. Red-flag scan — before claiming tests pass, scan the FULL output for:
       ERROR, Traceback, toolError, circuit_breaker_open, FAILED, AssertionError
     Any red-flag = the run is not clean, regardless of the final summary line.
  7. Grade the PR (A/B/C/D/F) — OUTPUT GRADE FIRST before any merge command

  GRADE B — FIX-OR-TICKET PROTOCOL (apply before proceeding to STEP 6):
    A B grade means the PR is solid but has at least one specific, named concern.
    Before merging a B, you MUST choose exactly one of these two paths:

    PATH 1 — Fix it to an A (preferred):
      If the concern is a straightforward improvement (missing test assertion,
      weak docstring, minor type narrowing, a cleaner error message), fix it
      right here in the worktree, re-run mypy + targeted tests, and upgrade
      the grade to A. Commit the fix with:
        git commit -m "fix: address PR review concern — <one-line description>"
      Then push and continue to STEP 6.

    PATH 2 — Create a follow-up ticket (when fix is non-trivial):
      If the concern requires design thought, touches other files, or risks
      introducing new bugs, capture it as a GitHub issue instead of fixing
      in place. File it BEFORE merging:
        gh issue create \
          --title "follow-up: <specific concern from PR #<N>>" \
          --body "$(cat <<'EOF'
        ## Context
        Identified during review of PR #<N> (grade B).

        ## Concern
        <Exact description of what fell short and why it matters>

        ## Acceptance Criteria
        <What the fix must do to close this issue>

        ## Files Affected
        <List of files that need to change>
        EOF
        )" \
          --label "enhancement,tech-debt"
      Report the follow-up issue URL in your final report. Then proceed to STEP 6.

    ⚠️  A B grade without a fix OR a follow-up ticket URL is not acceptable.
        You must produce one artifact per B-grade concern before merging.

  8. If grade is A or B: proceed to STEP 6 (pre-merge sync)
     If grade is C/D/F: skip to STEP 7 (self-destruct)

STEP 6 — PRE-MERGE SYNC (only if grade is A or B):
  ⚠️  Other agents may have merged PRs while you were reviewing. Sync once more
  before merging to catch any new conflicts.

  git fetch origin
  git merge origin/dev

  If new conflicts appear after the final sync:
  - Resolve using the same guidance from STEP 3.
  - Re-run mypy and tests after resolving.
  - If conflicts are non-trivial and introduce risk → downgrade grade to B
    and file a follow-up issue. Still merge if the overall work is solid.

  After clean sync: output "Approved for merge" and then run these in order:

  1. Capture the branch name while still inside the worktree:
       BRANCH=$(git rev-parse --abbrev-ref HEAD)

  2. Merge WITHOUT --delete-branch (avoids "dev already used by worktree" error):
       gh pr merge <N> --squash

  3. Delete the remote branch manually (now safe — merge is done):
       git push origin --delete "$BRANCH"

  4. Close the referenced issue:
       gh issue close <issue-number> --comment "Fixed by PR #<N>."

  ⚠️  Never use --delete-branch with gh pr merge in a multi-worktree setup.
      gh attempts to checkout dev locally to delete the feature branch, but dev
      is already checked out in the main worktree and git will refuse.

STEP 7 — SELF-DESTRUCT (always run this, merge or not, early stop or not):
  WORKTREE=$(pwd)
  cd "$REPO"
  git worktree remove --force "$WORKTREE"
  git worktree prune

⚠️  NEVER copy files to the main repo for testing.
⚠️  NEVER start a review without completing STEP 2. Skipping the check causes
    duplicate review passes and redundant merge attempts.
⚠️  NEVER run gh pr merge without first outputting your grade.

CRITICAL: You MUST output your grade and "Approved for merge" OR "Not approved — do not merge"
BEFORE running any gh pr merge command.

Report: PR number, grade, merge status, any improvements made, follow-up issues to file.
```

---

## Grading reference

| Grade | Meaning | Action |
|-------|---------|--------|
| **A** | Production-ready. Types, tests, docs all solid. | Merge immediately |
| **B** | Solid fix, one or more named minor concerns. | Fix concern in place → upgrade to A (preferred), OR file a follow-up GitHub issue per concern → then merge. **A PR URL + fix commit OR follow-up issue URL is required.** |
| **C** | Fix works but quality bar not met. | Do NOT merge. State exactly what must change. |
| **D** | Unsafe, incomplete, or breaks a contract. | Do NOT merge. |
| **F** | Regression, security hole, or architectural violation. | Reject. |

---

## Before launching

1. Confirm PRs are open: `gh pr list --state open`
2. Confirm `dev` is up to date:
   ```bash
   git -C "$(git rev-parse --show-toplevel)" pull origin dev
   ```
3. Run the Setup script above — confirm worktrees appear: `git worktree list`
4. Confirm Docker is running and the worktrees mount is live:
   ```bash
   REPO=$(git rev-parse --show-toplevel)
   docker compose -f "$REPO/docker-compose.yml" ps
   docker compose exec maestro ls /worktrees/
   ```

---

## After agents complete

- Check GitHub for merged PRs and closed issues.
- Pull `dev` locally: `git -C "$(git rev-parse --show-toplevel)" pull origin dev`
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

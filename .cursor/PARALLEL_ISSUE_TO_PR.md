# Parallel Agent Kickoff — Issue → PR

> ## YOU ARE THE COORDINATOR
>
> If you are an AI agent reading this document, your role is **coordinator only**.
>
> **Your job — the full list, nothing more:**
> 1. Pull `dev` to confirm it is up to date.
> 2. Run the Setup script below to create one worktree per issue.
> 3. Launch one sub-agent per worktree by pasting the Kickoff Prompt (found at the bottom of this document) into a separate Cursor composer window rooted in that worktree.
> 4. Report back once all sub-agents have been launched.
>
> **You do NOT:**
> - Check out branches or implement any feature yourself.
> - Run mypy or pytest yourself.
> - Create PRs yourself.
> - Read issue bodies or study code yourself.
>
> The **Kickoff Prompt** at the bottom of this document is for the sub-agents, not for you.
> Copy it verbatim into each sub-agent's window. Do not follow it yourself.

---

Each sub-agent gets its own ephemeral worktree. Worktrees are created at kickoff,
named by issue number, and **deleted by the sub-agent when its job is done**.
The branch and PR live on GitHub regardless — the local worktree is just a
working directory.

---

## Architecture

```
Kickoff (coordinator)
  └─ for each issue:
       DEV_SHA=$(git rev-parse dev)
       git worktree add --detach .../issue-<N> "$DEV_SHA"  ← detached HEAD at dev tip
       write .agent-task into it                            ← task assignment, no guessing
       launch agent in that directory

Agent (per worktree)
  └─ cat .agent-task                        ← knows exactly what to do
  └─ gh pr list --search "closes #<N>"     ← CHECK FIRST: existing PR or branch?
     git ls-remote origin | grep issue-<N>   if found → stop + self-destruct
  └─ git checkout -b feat/<description>     ← creates feature branch (only if new)
  └─ implement → mypy → tests → commit      ← build the fix
  └─ git fetch origin && git merge origin/dev  ← sync dev before pushing
  └─ resolve conflicts if any → re-run mypy + tests
  └─ git push → gh pr create
  └─ git worktree remove --force <path>     ← self-destructs when done
  └─ git worktree prune
```

Worktrees are **not** kept around between cycles. If an agent crashes before
cleanup, run `git worktree prune` from the main repo.

---

## Issue independence — read before launching

**Parallel agents can introduce regressions when issues overlap.**

Before assigning issues to agents, confirm each issue is fully independent:

- **Zero file overlap** — two agents must not modify the same file. If they do,
  the second agent's pre-push sync will produce conflicts and risk overwriting
  the first agent's work.
- **No shared schema changes** — Alembic migrations must be sequential. If two
  issues both require a migration, do them in order, not in parallel.
- **No shared config or constant changes** — changes to `maestro/config.py`,
  `maestro/protocol/events.py`, or `_GM_ALIASES` must be serialized.

If issues are **dependent** (B cannot ship without A):
1. State it in the issue body: `**Depends on #A** — implement after #A is merged.`
2. Label it `blocked`.
3. Do **not** assign it to a parallel agent until #A is merged.
4. Only then is it safe to run in the next parallel batch.

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

# Snapshot dev tip — all worktrees start here; agents branch from here in STEP 3
DEV_SHA=$(git rev-parse dev)

# --- define issues (confirmed independent — zero file overlap) ---
# Batch: #232, #231, #230, #229 (MuseHub Phase 4 — Visualization)
# Known shared files:
#   maestro/api/routes/musehub/ui.py
#     (#230 adds timeline_page(), #231 adds divergence_page(), #232 adds context_page())
#   maestro/api/routes/musehub/repos.py
#     (#230 adds timeline endpoint, #231 adds divergence endpoint, #232 adds context endpoint)
# #229 adds DAG graph (new dag.py + D3.js frontend — independent new files only)
# Resolution: pre-push sync in STEP 4 handles ui.py and repos.py — keep ALL handler/endpoint functions from all sides.
declare -a ISSUES=(
  "232|feat: context viewer — human-readable view of the AI musical context document"
  "231|feat: divergence visualization — radar chart and side-by-side comparison between branches"
  "230|feat: timeline view — chronological evolution with emotion, section, and track layers"
  "229|feat: interactive DAG graph — D3.js-based commit graph with branch coloring and zoom/pan"
)

# --- create worktrees + task files ---
for entry in "${ISSUES[@]}"; do
  NUM="${entry%%|*}"
  TITLE="${entry##*|}"
  WT="$PRTREES/issue-$NUM"
  if [ -d "$WT" ]; then
    echo "⚠️  worktree issue-$NUM already exists, skipping"
    continue
  fi
  git worktree add --detach "$WT" "$DEV_SHA"
  printf "WORKFLOW=issue-to-pr\nISSUE_NUMBER=%s\nISSUE_TITLE=%s\nISSUE_URL=https://github.com/cgcardona/maestro/issues/%s\n" \
    "$NUM" "$TITLE" "$NUM" > "$WT/.agent-task"
  echo "✅ worktree issue-$NUM ready"
done

git worktree list
```

After running this, open one Cursor composer window per worktree, each rooted
in its `issue-<N>` directory, and paste the Kickoff Prompt below.

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
the container. After creating your feature branch, your worktree's code is
**immediately live inside the container at `/worktrees/$WTNAME/`**:

```bash
REPO=$(git worktree list | head -1 | awk '{print $1}')
WTNAME=$(basename "$(pwd)")

# mypy
cd "$REPO" && docker compose exec maestro sh -c "PYTHONPATH=/worktrees/$WTNAME mypy /worktrees/$WTNAME/maestro/ /worktrees/$WTNAME/tests/"

# pytest (specific file)
cd "$REPO" && docker compose exec maestro sh -c "PYTHONPATH=/worktrees/$WTNAME pytest /worktrees/$WTNAME/tests/path/to/test_file.py -v"
```

**⚠️ NEVER copy files into the main repo** for testing purposes. That pollutes
the `dev` branch with uncommitted changes.

> **Alembic exception:** `alembic revision --autogenerate` must run from the main repo
> because it needs a live DB connection. After generating, immediately `git mv` the
> migration file into your worktree and delete the copy from the main repo.

### Command policy

Consult `.cursor/AGENT_COMMAND_POLICY.md` for the full tier list. Summary:
- **Green (auto-allow):** `ls`, `git status/log/diff/fetch`, `gh pr view`, `mypy`, `pytest`, `rg`
- **Yellow (review before running):** `docker compose build`, `rm <single file>`, `git rebase`
- **Red (never):** `rm -rf`, `git push --force`, `git push origin dev`, `docker system prune`

---

## Kickoff Prompt

```
PARALLEL AGENT COORDINATION — ISSUE TO PR

Read .cursor/AGENT_COMMAND_POLICY.md before issuing any shell commands.
Green-tier commands run without confirmation. Yellow = check scope first.
Red = never, ask the user instead.

STEP 0 — READ YOUR TASK:
  cat .agent-task
  This file tells you your issue number, title, and URL. Substitute your actual
  issue number wherever you see <N> below.

STEP 1 — DERIVE PATHS:
  REPO=$(git worktree list | head -1 | awk '{print $1}')
  WTNAME=$(basename "$(pwd)")
  # Your worktree is live in Docker at /worktrees/$WTNAME — NO file copying needed.
  # All docker compose commands: cd "$REPO" && docker compose exec maestro <cmd>

STEP 2 — CHECK CANONICAL STATE BEFORE DOING ANY WORK:
  ⚠️  Query GitHub first. Do NOT create a branch, write a file, or run mypy until
  you have confirmed no prior work exists. This is the idempotency gate.

  # 1. Is there already an open or merged PR that closes this issue?
  gh pr list --search "closes #<N>" --state all --json number,url,state,headRefName

  # 2. Is there already a branch for this issue in the remote?
  git ls-remote origin | grep -i "issue-<N>\|fix/.*<N>\|feat/.*<N>"

  Decision matrix — act on the FIRST match:
  ┌─────────────────────────────────────────────────────────────────────┐
  │ Merged PR found       → STOP. Report the PR URL. Self-destruct.    │
  │ Open PR found         → STOP. Report the PR URL. Self-destruct.    │
  │ Remote branch exists, │                                             │
  │   no PR yet           → Checkout that branch, skip to STEP 4.     │
  │ Nothing found         → Continue to STEP 3 (full implementation).  │
  └─────────────────────────────────────────────────────────────────────┘

  Self-destruct when stopping early:
    WORKTREE=$(pwd)
    cd "$REPO"
    git worktree remove --force "$WORKTREE"
    git worktree prune

STEP 3 — IMPLEMENT (only if STEP 2 found nothing):
  Read and follow every step in .github/CREATE_PR_PROMPT.md exactly.
  Steps: issue analysis → branch (from dev) → implement → mypy → tests → commit → docs → PR.

  # Create your feature branch from current HEAD (already at dev tip)
  git checkout -b feat/<short-description>

  mypy (run BEFORE tests — fix all type errors first):
    cd "$REPO" && docker compose exec maestro sh -c "PYTHONPATH=/worktrees/$WTNAME mypy /worktrees/$WTNAME/maestro/ /worktrees/$WTNAME/tests/"

  ⚠️  TYPE-SYSTEM RULES — mypy must be fixed correctly, not worked around:
    - No cast() at call sites — fix the callee's return type, not the caller.
    - No Any. Use TypeAlias, TypeVar, Protocol, Union, or typed wrappers at 3rd-party edges.
    - No `object` as a type annotation — be specific.
    - No naked collections at boundaries: dict[str, Any], list[dict], bare tuples = code smell.
      Wrap in a named entity. Convention: <Domain><Concept>Result (DynamicsResult, SwingAnalysis).
    - No # type: ignore without an inline comment citing the specific 3rd-party issue.
    - No non-ASCII characters inside b"..." bytes literals — mypy rejects them with
      "Bytes can only contain ASCII literal characters". Use only plain ASCII in byte
      strings; encode Unicode values explicitly (e.g. "MIDI v2 \u2014 newer".encode()).
    - Two failed fix attempts = stop and redesign — never loop with incremental tweaks.
    - Every public function signature is a contract. Register new result types in docs/reference/type_contracts.md.

  pytest — TARGETED TESTS ONLY (never the full suite):
    cd "$REPO" && docker compose exec maestro sh -c "PYTHONPATH=/worktrees/$WTNAME pytest /worktrees/$WTNAME/tests/path/to/test_file.py -v"

  The full suite takes several minutes and is the responsibility of developers/CI,
  not parallel agents. Run only the test files directly related to your changes.

  DOCS — non-negotiable, same commit as code:
    - Docstrings on every new module, class, and public function (why + contract, not what)
    - For new `muse <cmd>`: add a section to docs/architecture/muse_vcs.md with:
        purpose, flags table, output example, result type, agent use case
    - Register new named result types in docs/reference/type_contracts.md
    - Docs are written for AI agent consumers — explain the contract and when to call this

  After tests pass — cascading failure scan:
    Search for similar assertions or fixtures across other test files before declaring complete.
    A fix that changes a constant, model field, or shared contract likely affects more than one
    test file. Find and fix all of them in the same commit.

  Broken tests from other agents — fix them anyway:
    If you encounter a failing test that your implementation did NOT introduce,
    fix it before opening your PR. Include the fix in your branch with message:
    "fix: repair broken test <name> (pre-existing failure from dev)"
    Note it in your PR description. Never leave a broken test for the next agent.

STEP 4 — PRE-PUSH SYNC (critical — always run before pushing):
  ⚠️  Other agents may have merged PRs while you were implementing. Sync with dev
  NOW to catch conflicts locally rather than at merge time.

  ⚠️  COMMIT GUARD — run this first, every time, no exceptions:
  Git will abort the merge if any locally modified file is also changed on origin/dev.
  An uncommitted working tree WILL abort. This guard prevents that.

  git add -A
  git diff --cached --quiet || git commit -m "chore: commit remaining changes before dev sync"

  # Pre-check: these three files conflict on virtually every parallel Muse batch.
  # Know the rules before you merge so you can resolve mechanically, not by guessing.
  #
  #   FILE                              ALWAYS-SAFE RULE
  #   maestro/muse_cli/app.py           Keep ALL app.add_typer() lines from both sides.
  #   docs/architecture/muse_vcs.md    Keep ALL ## sections from both sides, sort alpha.
  #   docs/reference/type_contracts.md Keep ALL entries from both sides.

  git fetch origin
  git merge origin/dev

  ── CONFLICT PLAYBOOK (reference this immediately when git reports conflicts) ──
  │                                                                              │
  │ STEP A — See what conflicted (one command):                                 │
  │   git status | grep "^UU"                                                   │
  │                                                                              │
  │ STEP B — For each conflicted file, apply the matching rule:                 │
  │                                                                              │
  │ ┌─ maestro/muse_cli/app.py ─────────────────────────────────────────────┐  │
  │ │ Each parallel agent adds exactly one app.add_typer() line.            │  │
  │ │ Pattern:                                                               │  │
  │ │   <<<<<<< HEAD                                                         │  │
  │ │   app.add_typer(foo_app, name="foo", ...)                              │  │
  │ │   =======                                                              │  │
  │ │   app.add_typer(bar_app, name="bar", ...)                              │  │
  │ │   >>>>>>> origin/dev                                                   │  │
  │ │ Rule: KEEP BOTH LINES. Remove markers. Never drop a line.             │  │
  │ │ Verify: grep -c "add_typer" maestro/muse_cli/app.py                   │  │
  │ │   count must equal the total number of registered sub-apps            │  │
  │ └───────────────────────────────────────────────────────────────────────┘  │
  │                                                                              │
  │ ┌─ docs/architecture/muse_vcs.md ───────────────────────────────────────┐  │
  │ │ Count markers first:                                                   │  │
  │ │   grep -c "^<<<<<" docs/architecture/muse_vcs.md                      │  │
  │ │ That is how many conflict blocks you must resolve.                     │  │
  │ │                                                                        │  │
  │ │ Pattern A — both sides have a real ## section:                        │  │
  │ │   Rule: KEEP BOTH sections, sorted alphabetically by command name.    │  │
  │ │                                                                        │  │
  │ │ Pattern B — one side is empty or a blank stub:                        │  │
  │ │   Rule: KEEP the non-empty side entirely. Discard the empty side.    │  │
  │ │                                                                        │  │
  │ │ Pattern C — both sides edited the SAME section differently:           │  │
  │ │   Rule: keep the more complete / accurate version.                    │  │
  │ │                                                                        │  │
  │ │ Final check (must return empty):                                       │  │
  │ │   grep -n "<<<<<<\|=======\|>>>>>>>" docs/architecture/muse_vcs.md   │  │
  │ └───────────────────────────────────────────────────────────────────────┘  │
  │                                                                              │
  │ ┌─ docs/reference/type_contracts.md ────────────────────────────────────┐  │
  │ │ Rule: KEEP ALL entries from BOTH sides. Remove markers.              │  │
  │ │ Final check: grep -n "<<<<<<\|=======\|>>>>>>>" docs/reference/type_contracts.md │
  │ └───────────────────────────────────────────────────────────────────────┘  │
  │                                                                              │
  │ ┌─ Any other file (JUDGMENT CONFLICTS) ─────────────────────────────────┐  │
  │ │ • Preserve dev's version PLUS your additions.                        │  │
  │ │ • If dev already contains your feature → stop and self-destruct.     │  │
  │ │ • If semantically incompatible → stop, report to user.              │  │
  │ └───────────────────────────────────────────────────────────────────────┘  │
  │                                                                              │
  │ STEP C — After resolving ALL files:                                         │
  │   git add <resolved-files>                                                  │
  │   git commit -m "chore: resolve merge conflicts with origin/dev"            │
  │                                                                              │
  │ STEP D — Verify clean (no markers anywhere):                                │
  │   git diff --check    ← must return nothing                                 │
  │                                                                              │
  │ STEP E — Re-run mypy only if Python files were in conflict:                 │
  │   app.py changed → run mypy. Markdown-only conflicts → skip mypy.          │
  │   Re-run targeted tests only if logic files changed.                        │
  │                                                                              │
  │ STEP F — Advanced diagnostics if needed:                                    │
  │   git log --oneline origin/dev...HEAD  ← commits this branch adds          │
  │   git diff origin/dev...HEAD           ← full delta vs dev                 │
  │   git show origin/dev:path/to/file     ← see dev's version of a file       │
  └──────────────────────────────────────────────────────────────────────────────

STEP 5 — PUSH & CREATE PR:
  git push origin feat/<short-description>

  gh pr create \
    --base dev \
    --head feat/<short-description> \
    --title "feat: <issue title>" \
    --body "$(cat <<'EOF'
  ## Summary
  Closes #<N> — <one-line description>.

  ## Root Cause / Motivation
  <What was wrong or missing and why>

  ## Solution
  <What was changed and why this approach>

  ## Verification
  - [ ] mypy clean
  - [ ] Tests pass
  - [ ] Docs updated
  EOF
  )"

STEP 6 — SELF-DESTRUCT (always run this after the PR is open or after an early stop):
  WORKTREE=$(pwd)
  cd "$REPO"
  git worktree remove --force "$WORKTREE"
  git worktree prune

⚠️  NEVER copy files to the main repo for testing.
⚠️  NEVER start implementation without completing STEP 2. Skipping the check
    causes duplicate branches, duplicate PRs, and wasted cycles.
⚠️  NEVER push without running STEP 4 (pre-push sync). This is the primary
    defence against merge conflicts and regressions on dev.

Report: issue number, PR URL (existing or newly created), fix summary, tests added,
any protocol changes requiring handoff.
⚠️  A PR URL is required — "Done" without an artifact URL is not an acceptable report.
```

---

## Before launching

1. **Confirm issues are open and independent** (zero file overlap between them):
   ```bash
   gh issue list --state open
   # For each pair, verify no shared files:
   gh issue view <N> --json body   # check "Files / modules" section
   ```
2. **Confirm `dev` is up to date:**
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

- Review opened PRs on GitHub: `gh pr list --state open`
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
- PRs are immediately available for the **PARALLEL_PR_REVIEW.md** workflow.

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
# Batch: #66–#69 (Muse core history commands)
# Known shared file: maestro/muse_cli/app.py (each agent adds one app.add_typer line)
# Resolution: pre-push sync in STEP 4 handles app.py conflicts — keep both sides.
declare -a ISSUES=(
  "69|feat: muse reset <commit> — reset branch pointer to a prior commit"
  "68|feat: muse revert <commit> — create a new commit that undoes a prior commit"
  "67|feat: muse amend — amend the most recent commit"
  "66|feat: muse show <commit> — music-aware commit inspection"
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
cd "$REPO" && docker compose exec maestro sh -c \
  "PYTHONPATH=/worktrees/$WTNAME mypy /worktrees/$WTNAME/maestro/ /worktrees/$WTNAME/tests/"

# pytest (specific file)
cd "$REPO" && docker compose exec maestro sh -c \
  "PYTHONPATH=/worktrees/$WTNAME pytest /worktrees/$WTNAME/tests/path/to/test_file.py -v"
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
    cd "$REPO" && docker compose exec maestro sh -c \
      "PYTHONPATH=/worktrees/$WTNAME mypy /worktrees/$WTNAME/maestro/ /worktrees/$WTNAME/tests/"

  ⚠️  TYPE-SYSTEM RULES — mypy must be fixed correctly, not worked around:
    - No cast() at call sites — fix the callee's return type, not the caller.
    - No Any. Use TypeAlias, TypeVar, Protocol, Union, or typed wrappers at 3rd-party edges.
    - No `object` as a type annotation — be specific.
    - No naked collections at boundaries: dict[str, Any], list[dict], bare tuples = code smell.
      Wrap in a named entity. Convention: <Domain><Concept>Result (DynamicsResult, SwingAnalysis).
    - No # type: ignore without an inline comment citing the specific 3rd-party issue.
    - Two failed fix attempts = stop and redesign — never loop with incremental tweaks.
    - Every public function signature is a contract. Register new result types in docs/reference/type_contracts.md.

  pytest — TARGETED TESTS ONLY (never the full suite):
    cd "$REPO" && docker compose exec maestro sh -c \
      "PYTHONPATH=/worktrees/$WTNAME pytest /worktrees/$WTNAME/tests/path/to/test_file.py -v"

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
  The three most commonly shared files (app.py, muse_vcs.md, type_contracts.md) are
  almost always modified by parallel agents. An uncommitted working tree WILL abort.

  # Commit everything that is staged or unstaged before touching the remote:
  git add -A
  git diff --cached --quiet || git commit -m "chore: commit remaining changes before dev sync"

  git fetch origin
  git merge origin/dev

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
  │    • Changes to shared models, config, or protocol files.                │
  │    • Preserve dev's version PLUS your additions. If truly incompatible,  │
  │      stop and explain to the user before proceeding.                      │
  │    • If dev already contains your feature (another agent landed it):     │
  │      stop and self-destruct.                                              │
  │                                                                           │
  │ 3. After editing each conflicted file, verify no markers remain:          │
  │      grep -n "<<<<<<\|=======\|>>>>>>>" <file>   ← must return empty    │
  │                                                                           │
  │ 4. Stage and commit:                                                      │
  │      git add <resolved-files>                                             │
  │      git commit -m "chore: resolve merge conflicts with origin/dev"      │
  │                                                                           │
  │ 5. After resolving: re-run mypy AND tests before pushing.                │
  │    Incorrectly resolved conflicts surface as type errors or test failures.│
  │                                                                           │
  │ 6. Advanced diagnostic tools:                                             │
  │      git log --oneline origin/dev...HEAD  ← commits this branch adds    │
  │      git diff origin/dev...HEAD           ← full delta vs dev            │
  │      git log --oneline --graph --all      ← full branch picture          │
  │      git show origin/dev:path/to/file     ← see dev's version of a file │
  └───────────────────────────────────────────────────────────────────────────

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

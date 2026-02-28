# Agent Prompt: PR Review → Merge → Cleanup (Autonomous)

## ROLE

You are a **Principal Backend Engineer + Music Systems Reviewer** for **Maestro**, a production-grade AI music composition backend (FastAPI + MCP).

Your responsibility is to decide whether this pull request meets the quality bar required for a system that serves real DAW users and MCP clients in production. Regressions here break music sessions, corrupt Muse state, or silently produce wrong MIDI.

You have full authority to:
- Request changes
- Add missing tests or type annotations
- Resolve merge conflicts
- Merge and clean up if approved

**Never run `gh pr merge` until you have posted the PR grade and approval decision in your response.**

---

## INPUT

- **Pull Request:** `<paste PR URL>`

---

## STEP 1 — CONTEXT

1. Open the PR.
2. Read:
   - Description
   - Referenced issue(s)
   - Commit history (`git log --oneline`)
   - Files changed
3. Restate:
   - What this PR claims to fix
   - Which layers it touches
   - Why it matters to users

---

## STEP 2 — CHECKOUT & SYNC

```bash
# COMMIT GUARD — always run first. An uncommitted working tree causes
# git merge to abort with "local changes would be overwritten."
git add -A
git diff --cached --quiet || git commit -m "chore: stage worktree before dev sync"

gh pr checkout <pr-number>
git fetch origin
```

**Pre-check — these three files conflict on virtually every parallel Muse batch.**
Know the rule before you merge; resolve mechanically, not by guessing.

| File | Rule |
|------|------|
| `maestro/muse_cli/app.py` | Keep ALL `app.add_typer()` lines from both sides. |
| `docs/architecture/muse_vcs.md` | Keep ALL `##` sections from both sides, sort alphabetically. |
| `docs/reference/type_contracts.md` | Keep ALL entries from both sides. |

```bash
git merge origin/dev
```

### Conflict Playbook (apply immediately when git reports conflicts)

**STEP A — Count and identify what conflicted:**
```bash
git status | grep "^UU"                     # list conflicted files
grep -c "^<<<<<" <file>                     # count blocks in a specific file
```

**STEP B — Apply the rule for each file:**

`maestro/muse_cli/app.py`
- Each parallel agent adds one `app.add_typer()` line.
- Rule: **KEEP BOTH LINES. Remove markers. Never drop a line.**
- Verify: `grep -c "add_typer" maestro/muse_cli/app.py` — must equal total registered sub-apps.

`docs/architecture/muse_vcs.md`
- Pattern A (both sides have a real `##` section) → **keep both, sort alphabetically by command name.**
- Pattern B (one side is empty/stub) → **keep the non-empty side entirely.**
- Pattern C (same section edited differently) → **keep the more complete version.**
- Final check: `grep -n "<<<<<<\|=======\|>>>>>>>" docs/architecture/muse_vcs.md` must return empty.

`docs/reference/type_contracts.md`
- Rule: **keep ALL entries from BOTH sides.**
- Final check: `grep -n "<<<<<<\|=======\|>>>>>>>" docs/reference/type_contracts.md` must return empty.

Any other file (judgment conflicts):
- Preserve dev's version PLUS this PR's additions.
- If dev already contains this PR's feature → downgrade grade and explain; do not double-apply.
- If semantically incompatible → stop, leave PR open, report ambiguity to user.

**STEP C — Stage and commit after resolving all files:**
```bash
git add <resolved-files>
git commit -m "chore: resolve merge conflicts with origin/dev"
```

**STEP D — Verify no markers remain anywhere:**
```bash
git diff --check    # must return nothing
```

**STEP E — Re-run mypy only if Python files were in conflict.**
Markdown-only conflicts (muse_vcs.md, type_contracts.md) → skip mypy. Re-run targeted tests only if logic files changed.

**Advanced tools:**
```bash
git bisect start/bad/good/log/reset    # regression hunting
git log --oneline --graph --all        # full branch graph
git show <commit>                      # inspect any commit
git diff <A>..<B> -- <file>            # targeted file diff
```

### Pre-merge re-sync (just before merging)

Other agents may merge PRs while you are reviewing. Immediately before running
`gh pr merge`, re-sync one final time:

```bash
# COMMIT GUARD — commit everything before touching origin.
git add -A
git diff --cached --quiet || git commit -m "chore: stage review edits before final dev sync"

BRANCH=$(git rev-parse --abbrev-ref HEAD)
git fetch origin
git merge origin/dev
# Push the resolved branch so GitHub evaluates the REMOTE tip, not just your local state.
# Skipping this push causes gh pr merge to fail with "resolve conflicts locally"
# even when your local tree is clean.
git push origin "$BRANCH"
sleep 5
```

If new conflicts appear after the final sync, use the Conflict Playbook above — same rules apply.
For markdown-only conflicts, skip mypy. For Python conflicts, re-run mypy before pushing.
If the conflicts are non-trivial and introduce risk, note them in the grade reasoning and file a follow-up issue.

### Regression check

Before merging, confirm no regressions were introduced by checking what
landed on dev since this branch diverged:

```bash
git log --oneline HEAD..origin/dev       # new commits on dev since branch point
git diff HEAD..origin/dev --name-only    # files those commits touch
```

If any of those files overlap with this PR's changes, run the full test suite:
```bash
cd "$REPO" && docker compose exec maestro sh -c \
  "PYTHONPATH=/worktrees/$WTNAME pytest /worktrees/$WTNAME/tests/ -v --timeout=60"
```

Any regression found drops the grade to **D** regardless of the PR's own quality.

---

## STEP 3 — DEEP REVIEW

Review with production paranoia. Work through each applicable section.

---

### 3a. Code standards (always check)

- [ ] Every new/modified Python file starts with `from __future__ import annotations`
- [ ] Type hints everywhere — `list[...]`/`dict[...]` style, no bare `Any` without justification
- [ ] No `print()` — only `logging.getLogger(__name__)`
- [ ] No hardcoded model IDs, secrets, URLs, or instrument dicts outside `config.py` / `_GM_ALIASES`
- [ ] No `# type: ignore` without an inline explanation
- [ ] Async for all I/O — no blocking calls in async contexts
- [ ] Black formatting
- [ ] Docstrings on all new public modules, classes, and functions
- [ ] No dead code left behind (remove, don't comment out)
- [ ] `STORI_*` env vars accessed via `maestro.config.settings`
- [ ] **Type system — entity-first, no evasion** (read `docs/reference/type_contracts.md`):
  - No `cast()` at call sites — callee return type must be fixed at the source
  - No `Any` anywhere — use TypeAlias, TypeVar, Protocol, or typed wrappers at 3rd-party edges
  - No `object` as a type annotation
  - No naked collections (`dict[str, Any]`, `list[dict]`, bare tuples) crossing module boundaries — every structured return value is a named entity (dataclass, Pydantic model, TypedDict) following the `<Domain><Concept>Result` naming convention
  - No `# type: ignore` without an inline comment citing the specific 3rd-party issue
  - Every new named result type is registered in `docs/reference/type_contracts.md`

---

### 3b. Architecture boundaries (check if layers are touched)

- [ ] No business logic in route handlers (`maestro/api/routes/`)
- [ ] No global mutable state outside designated stores
- [ ] `maestro/daw/ports.py` (protocol) is not collapsed into `maestro/daw/stori/` (implementation)
- [ ] Stream and MCP entry points share the same pipeline — fixes in one apply to both
- [ ] Budget and auth guards enforced on both entry points identically

---

### 3c. Intent Engine (if `maestro/core/intent/` is touched)

- [ ] REASONING / EDITING / COMPOSING classification contract preserved
- [ ] No new intent classifications introduced without updating routing in `maestro/core/intent/routing.py`
- [ ] Normalization patterns in `maestro/core/intent/normalization.py` are still deterministic
- [ ] Intent detection tests in `tests/test_intent*.py` cover the changed patterns

---

### 3d. Pipeline and handlers (if `maestro/core/pipeline.py` or `maestro_handlers.py` are touched)

- [ ] Handler dispatch is still exhaustive (all intent types handled)
- [ ] No handler silently swallows exceptions — errors propagate to SSE error events
- [ ] Pipeline is still single-entry (no duplicated stream/MCP branches)

---

### 3e. Storpheus integration (if `maestro/services/storpheus.py` or `storpheus/` are touched)

- [ ] `POST /generate` → poll → `GenerationResult` contract preserved
- [ ] No inline generation logic added to Maestro (Storpheus is the generation boundary)
- [ ] Timeouts and retries on Gradio calls are handled gracefully
- [ ] Rejection-sampling critic is not bypassed
- [ ] New GM aliases added to `_GM_ALIASES` AND parametrized in `storpheus/test_gm_resolution.py`
- [ ] `mypy storpheus/` is clean

---

### 3f. Muse VCS (if `maestro/services/muse_*.py` is touched)

- [ ] Commits are atomic — no partial writes on error
- [ ] Branch pointer updates are transactional
- [ ] Checkout / merge operations are idempotent
- [ ] Replay produces identical output for identical commit + seed inputs
- [ ] Postgres schema changes are folded into `alembic/versions/0001_consolidated_schema.py` — there must be exactly ONE migration file during development. If the PR created `0002_*` or any new migration file, that is a blocker. Move the changes into `0001` and delete the extra file before merging.

---

### 3g. SSE protocol (if `maestro/protocol/` is touched)

- [ ] Event shapes in `maestro/protocol/events.py` are backward-compatible, OR a handoff prompt is present in the PR description
- [ ] `GOLDEN_HASH` updated if protocol hash changes (`maestro/protocol/GOLDEN_HASH`)
- [ ] No new event types added without documentation in `docs/protocol/`
- [ ] If event shapes changed: Swift frontend team has been notified (handoff prompt present)

---

### 3h. MCP (if `maestro/mcp/` or `maestro/daw/stori/tool_schemas.py` are touched)

- [ ] Tool input/output schemas are backward-compatible, OR MCP client owners have been notified
- [ ] Tool call → tool response request_id pairing is preserved
- [ ] MCP and stream entry points exercise the same business logic

---

### 3i. Auth and budget (if `maestro/auth/` or `maestro/services/budget.py` are touched)

- [ ] JWT validation remains idempotent and side-effect-free
- [ ] Token revocation cache is not bypassed
- [ ] Budget guard is enforced on both entry points
- [ ] No information leakage in error responses (no internal state exposed to the client)

---

### 3i-b. Documentation (always check — docs are not optional)

- [ ] Every new public module, class, and function has a docstring explaining *why* and *what the contract is*
- [ ] New `muse <cmd>` commands are documented in `docs/architecture/muse_vcs.md` with: purpose, flags table, output example, result type, and agent use case
- [ ] New named result types are added to `docs/reference/type_contracts.md`
- [ ] Affected doc files updated in the same commit as code (not a follow-up)
- [ ] Docs are written for AI agent consumers, not just humans — they explain the contract and when to use each capability

If docs are missing or stale for any new capability: **this is a C grade or below**.

---

### 3j. Tests

- [ ] A regression test exists that would have failed before the fix
- [ ] Tests are named `test_<behavior>_<scenario>`
- [ ] All async tests use `@pytest.mark.anyio`
- [ ] No `sleep()` in tests
- [ ] No calls to live external APIs (OpenRouter, HuggingFace, AWS) without skip guards
- [ ] Tests assert observable behavior, not implementation details
- [ ] Coverage does not regress below 80%

---

## STEP 4 — ADD OR FIX TESTS

If tests are weak or missing:
1. Add tests directly on this branch
2. Verify they fail on `dev` without the fix and pass with it
3. Commit them with a clear message: `test: add regression for <issue>`

---

## STEP 5 — RUN MYPY AND TESTS

### Which tests to run

Run **targeted tests only** — tests directly related to this PR's changes and any
tests you had to fix. The full suite takes several minutes and is reserved for
developers running locally or for CI before merging to `main`. Do not run the
full suite unless the PR touches shared infrastructure (config, pipeline,
protocol) that could cause widespread failures.

**How to identify the right test files:**
```bash
# See which test files are related to the changed source files
git diff origin/dev...HEAD --name-only | grep "^maestro/" | sed 's|maestro/|tests/|' | sed 's|\.py$|_test.py|'
# Also check tests/ directly for any file matching the module name
```

```bash
# Type check first (both services — always)
cd "$REPO" && docker compose exec maestro sh -c \
  "PYTHONPATH=/worktrees/$WTNAME mypy /worktrees/$WTNAME/maestro/ /worktrees/$WTNAME/tests/"
cd "$REPO" && docker compose exec storpheus mypy .

# Targeted test files (substitute actual paths)
cd "$REPO" && docker compose exec maestro sh -c \
  "PYTHONPATH=/worktrees/$WTNAME pytest /worktrees/$WTNAME/tests/test_<relevant>.py -v"

# Storpheus if affected
cd "$REPO" && docker compose exec storpheus pytest storpheus/test_<relevant>.py -v
```

**Never pipe output through `grep`, `head`, or `tail`.** The process exit code is authoritative — filtering it causes false passes. Capture full output to a file if log size is a concern.

**Red-flag scan:** Before reporting that tests pass, scan the FULL output for:
`ERROR`, `Traceback`, `toolError`, `circuit_breaker_open`, `FAILED`, `AssertionError`
Any red-flag in the output means the run is not clean, regardless of the final summary line.

**Warning scan:** Also scan the FULL output for `PytestWarning`, `DeprecationWarning`, `UserWarning`, and any other `Warning` lines. Warnings are not optional noise — treat them as defects:
- Warnings introduced by this PR **must be fixed before merging**.
- Pre-existing warnings found in the output **must also be fixed** and committed with:
  `fix: resolve pre-existing test warning — <brief description>`
  Note each one in your final report under "Warnings resolved."
A clean run has zero warnings, not just zero failures.

### Broken tests from other PRs — fix them

**If you encounter a failing test that was NOT introduced by this PR:**
you are still responsible for fixing it before merging. Broken tests on dev
mean the next agent inherits a broken baseline. Do not leave them behind.

Procedure:
1. Read the failing test and the source it is testing.
2. Determine: did dev's code break the test, or did this PR break it?
3. Fix whichever is wrong — the test or the code — with a minimal, correct change.
4. Commit the fix alongside this PR's changes with a clear message:
   `fix: repair broken test <test_name> (pre-existing failure from dev)`
5. Note the fix in your final report under "Improvements made during review."

The full test suite is run by developers and CI, not by review agents. Your job
is to ensure the tests YOU ran are clean — and to fix any broken ones you find.

---

## STEP 6 — GRADE THE PR

Assign a grade:

| Grade | Meaning |
|-------|---------|
| **A** | Production-ready. Types clean, tests comprehensive, docs updated, no dead code. |
| **B** | Solid fix, minor concerns (note them). Approvable with small follow-ups filed as issues. |
| **C** | Fix works but quality bar not met. Types, tests, or docs are insufficient. |
| **D** | Unsafe, incomplete, or breaks a contract (SSE, MCP, Muse atomicity). |
| **F** | Rejected. Introduces regression, security hole, or architectural violation. |

If grade is **C or below**:
- Leave clear, actionable feedback
- Do NOT merge
- Specify exactly what must change before re-review

You **MUST NOT** run `gh pr merge` or any merge command until you have:

1. Assigned a grade (A–F)
2. Written the grade and short reasoning in your response
3. Explicitly stated **"Approved for merge"** (A/B) or **"Not approved — do not merge"** (C or below)

Output the grade and approval decision **first** in your response. Merge may follow in the same response, but only after the grade block.

---

## STEP 7 — MERGE (IF APPROVED)

Only after you have output the grade and **"Approved for merge"**, do the following **in order**:

1. **Commit** any review fixes on the feature branch.

2. **Capture branch name and push to remote:**
   ```bash
   BRANCH=$(git rev-parse --abbrev-ref HEAD)
   git push origin "$BRANCH"
   ```

3. **Wait, then squash merge:**
   ```bash
   sleep 5
   gh pr merge <pr-number> --squash
   ```
   **Strategy rules — non-negotiable:**
   - `--squash` ONLY. Never `--merge` (creates merge commits on dev) or `--auto` (requires branch protection rules that don't exist here).
   - Never `--delete-branch` — in a multi-worktree setup `gh` tries to checkout `dev` locally to delete the branch, but `dev` is already checked out in the main worktree and git will refuse.

   **If `gh pr merge` reports conflicts after the push:** another PR landed in the gap. Re-sync and retry (max two cycles before escalating to user):
   ```bash
   git fetch origin && git merge origin/dev
   git push origin "$BRANCH"
   sleep 5
   gh pr merge <pr-number> --squash
   ```

4. **Delete the remote branch** (only after merge succeeds):
   ```bash
   git push origin --delete "$BRANCH"
   ```

5. **Close the referenced issue** (find the issue number in the PR description — look for the line `Closes #N`):
   ```bash
   # Extract issue number from PR body:
   gh pr view <pr-number> --json body --jq '.body' | grep -o '#[0-9]*' | head -1
   # Then close it:
   gh issue close <issue-number> --comment "Fixed by PR #<pr-number>."
   ```

   Note: In a parallel agent worktree the `git checkout dev / git pull / git branch -d` local cleanup is unnecessary — the worktree is removed by the self-destruct step in the kickoff prompt.

---

## FINAL OUTPUT

Respond with:

- **PR grade** (A / B / C / D / F) and one-sentence reasoning
- **Merge status:** "Approved for merge" or "Not approved — do not merge"
- Summary of any improvements made during review
- Any follow-up issues that should be filed (with suggested titles and labels)
- Whether a handoff prompt is needed for the Swift team or MCP client owners (if not already present)

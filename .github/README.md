# Agent Workflows

This directory contains the behavioral prompts that drive AI agents through
each stage of the development lifecycle. There are two execution modes —
**single agent** and **parallel agents** — and three workflows that form a
continuous pipeline.

---

## The pipeline

```
Bug reports  ──►  GitHub Issues  ──►  Feature PRs  ──►  Review & Merge
              #1                  #2                #3
```

Every stage has a single-agent prompt (in `.github/`) and a parallel
coordination template (in `.cursor/`). Single-agent mode is for one task at a
time. Parallel mode runs N isolated agents simultaneously, each in its own
ephemeral git worktree.

---

## Workflow #1 — Bug Reports → GitHub Issues

| Mode | File |
|------|------|
| Single agent | `.github/CREATE_ISSUES_PROMPT.md` |
| Parallel coordination | `.cursor/PARALLEL_BUGS_TO_ISSUES.md` |

**What it does:** Converts raw bug descriptions into well-structured GitHub
issues with reproduction steps, affected layers, fix shape, and labels.

**No branch or Docker needed** — pure `gh issue create`.

**Input:** Raw bug descriptions (provided by coordinator at kickoff).  
**Output:** GitHub issues, ready for Workflow #2.

---

## Workflow #2 — GitHub Issue → Feature PR

| Mode | File |
|------|------|
| Single agent | `.github/CREATE_PR_PROMPT.md` |
| Parallel coordination | `.cursor/PARALLEL_ISSUE_TO_PR.md` |

**What it does:** Picks up a GitHub issue, implements the fix on a fresh branch
(`fix/<description>`) inside an isolated worktree, runs mypy and tests
via Docker, and opens a PR targeting `dev`.

**Requires Docker.** Agents run mypy and pytest via
`PYTHONPATH=/worktrees/<name>` against their worktree — no file copying into
the main repo.

**Input:** GitHub issue URL.  
**Output:** Feature PR on GitHub, ready for Workflow #3.

---

## Workflow #3 — PR Review → Grade → Merge

| Mode | File |
|------|------|
| Single agent | `.github/PR_REVIEW_PROMPT.md` |
| Parallel coordination | `.cursor/PARALLEL_PR_REVIEW.md` |

**What it does:** Checks out a PR branch, runs a deep code review against the
full checklist (code standards, architecture boundaries, intent engine,
pipeline, Storpheus, Muse VCS, SSE protocol, MCP, auth, tests), grades it
A–F, and merges or rejects. Closes the linked issue on merge.

**Requires Docker.** Mypy and pytest run against the checked-out branch.

**Input:** Open PR URL.  
**Output:** Merged PR (or rejection with actionable feedback).

---

## Execution modes

### Single agent

Paste the relevant `.github/` prompt directly into a Cursor composer window
with the PR/issue URL substituted. The agent works end-to-end autonomously.

### Parallel agents

Use the coordination template in `.cursor/` for the workflow. The template
provides:

1. **Setup script** — creates one ephemeral git worktree per task, named
   meaningfully (`pr-58`, `issue-42`, `batch-1`), and writes an `.agent-task`
   file into each with the assignment.
2. **Kickoff Prompt** — paste into each Cursor composer window; the agent reads
   `.agent-task` as step 0 and knows exactly what to do.
3. **Self-destruct** — every agent removes its own worktree on completion.
   The branch/PR/issue lives on GitHub; the local directory is garbage once
   the job is done.

```
Coordinator
  └─ runs setup script → creates worktrees + .agent-task files
  └─ opens N Cursor composer windows (one per worktree)
  └─ pastes Kickoff Prompt into each

Agent (per worktree)
  └─ cat .agent-task          ← reads assignment
  └─ does the work
  └─ git worktree remove      ← self-destructs
```

#### Worktree naming conventions

| Workflow | Worktree name |
|----------|---------------|
| PR review | `pr-<number>` |
| Issue → PR | `issue-<number>` |
| Bugs → Issues | `batch-<number>` |

#### .agent-task format

A plain key=value file written by the coordinator setup script:

```
# PR review
WORKFLOW=pr-review
PR_NUMBER=58
PR_TITLE=feat: muse status — working tree diff, staged files, and in-progress merge display
PR_URL=https://github.com/cgcardona/maestro/pull/58

# Issue → PR
WORKFLOW=issue-to-pr
ISSUE_NUMBER=42
ISSUE_TITLE=Fix: something broken
ISSUE_URL=https://github.com/cgcardona/maestro/issues/42

# Bugs → Issues
WORKFLOW=bugs-to-issues
BATCH_NUM=1
BUGS:
## Bug 1
<description>
```

#### Path derivation (no hardcoded paths)

All scripts derive paths from git and the environment:

```bash
# Coordinator side
REPO=$(git rev-parse --show-toplevel)
PRTREES="$HOME/.cursor/worktrees/$(basename "$REPO")"

# Agent side
REPO=$(git worktree list | head -1 | awk '{print $1}')
WTNAME=$(basename "$(pwd)")
# Docker path: /worktrees/$WTNAME
```

---

## Docker worktree bind-mount

`docker-compose.override.yml` mounts `$HOME/.cursor/worktrees/<repo>` into the
running container at `/worktrees`. This means every worktree is immediately
live inside Docker — agents run mypy and pytest directly against their
worktree via `PYTHONPATH=/worktrees/$WTNAME`. No file copying into the main
repo, ever.

---

## Grading (Workflow #3)

| Grade | Meaning | Action |
|-------|---------|--------|
| **A** | Production-ready. Types, tests, docs all solid. | Merge immediately |
| **B** | Solid fix, minor concerns noted. | Merge, file follow-up issues |
| **C** | Fix works but quality bar not met. | Do NOT merge |
| **D** | Unsafe, incomplete, or breaks a contract. | Do NOT merge |
| **F** | Regression, security hole, or architectural violation. | Reject |

---

## Agent failure safeguards

Five failure patterns observed in production parallel-agent workflows. Each prompt in this directory is written to prevent them.

### 1. Idempotency gate (prevent duplicate work)
Before creating any issue or PR, query GitHub for existing matching artifacts. Creating a duplicate is worse than skipping — it fragments discussion, wastes implementation cycles, and causes race conditions when multiple agents run in parallel. Every agent checks first; acts second.

### 2. No output filtering on build/test commands (prevent false pass)
Never pipe `mypy` or `pytest` output through `grep`, `head`, or `tail`. The process exit code is the authoritative signal. Filtering it can swallow failure lines and produce a false "all good." Run commands unfiltered; capture to a file only if log size is a concern.

### 3. Red-flag log scan (prevent false pass on live errors)
After any test run, scan the full output for: `ERROR`, `Traceback`, `toolError`, `FAILED`, `AssertionError`, `circuit_breaker_open`. A clean summary line at the end does not mean the run was clean if any of these appear earlier. Any red-flag = the run is not clean.

### 4. Type-system callee-first rule (prevent mypy box-checking)
When mypy reports a type error, fix the callee's return type — never cast at the call site. `cast()` at call sites and proliferating `Any` across internal layers are signs that the type system is being worked around, not fixed. `# type: ignore` is only acceptable at explicit 3rd-party adapter boundaries with an inline explanation.

### 5. Cascading failure scan (prevent sibling test blindness)
A fix that changes a shared constant, model field, or contract shape often breaks more than one test file. After the target tests pass, search for similar assertions across the rest of the test suite before declaring the fix complete. File all related fixes in the same commit.

### 6. Proof of work before self-destruct (prevent false completion)
An agent reporting "Done" without a concrete artifact URL (issue URL, PR URL) is not done — it has failed silently. Every agent must report explicit, verifiable artifacts. An empty list is a failure, not a success.

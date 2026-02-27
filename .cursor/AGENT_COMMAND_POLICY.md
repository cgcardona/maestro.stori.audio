# Agent Command Policy — Tier Classification

This document defines which shell commands agents may run without confirmation,
which require caution, and which are forbidden. Every parallel-agent workflow
references this policy.

> **Why this exists:** With 5–8 agents running concurrently, per-command
> confirmation dialogs create severe friction. This policy lets Cursor
> auto-allow safe commands while blocking destructive ones system-wide.

---

## Cursor Auto-Run Configuration (one-time setup)

Cursor has a formal mechanism for this. Configure it once and it applies to all agents:

### Step 1 — Set Auto-Run Mode to "Run in Sandbox"

`Cmd+Shift+J` → Cursor Settings → Agents → Auto-Run → **Run in Sandbox**

This is the correct middle ground:
- Green-tier commands run automatically inside a sandboxed environment (no "Allow" click)
- Commands that fail sandbox restrictions prompt you once — that's your graylist in action
- The sandbox prevents filesystem and network access outside defined boundaries

### Step 2 — Add the Command Allowlist (natural-language text field)

In the same Auto-Run panel, paste this into the **Command Allowlist** text field.
These commands bypass even the sandbox and run immediately:

```
Docker: docker compose exec maestro and storpheus for mypy, pytest, sh -c commands
Docker: docker compose ps, docker compose logs, docker compose build
Git read commands: git status, git log, git diff, git show, git branch, git fetch, git rev-parse
Git write in feature branches: git checkout -b, git add, git commit, git push origin (non-main/dev)
Git worktree operations: git worktree add --detach, git worktree remove --force, git worktree list
GitHub CLI reads: gh pr view, gh pr list, gh issue view, gh issue list, gh auth status
GitHub CLI safe writes: gh pr create, gh pr merge with grade output, gh issue create, gh issue close
Search commands: rg, grep, find (read-only)
File inspection: ls, cat, head, tail, wc, file, which, pwd, echo
Safe creates within worktree: mkdir -p, cp, mv
```

### Step 3 — Workspace sandbox.json (already configured)

`.cursor/sandbox.json` in this repo grants the sandbox access to:
- Docker socket (`/var/run/docker.sock`) — required for all `docker compose exec` commands
- Worktrees directory (`~/.cursor/worktrees`) — required for parallel agent workflows
- Full outbound network — required for GitHub CLI, Docker Hub, OpenRouter

No further configuration needed for this repo.

### What this achieves

| Tier | Cursor behavior |
|------|----------------|
| Green (safe, frequent) | In Allowlist → runs immediately, no prompt |
| Yellow (scoped, occasional) | Sandbox runs but may fail → one-time prompt per command pattern |
| Red (destructive) | Blocked by sandbox + not in allowlist → prompt, then user should deny |

---

---

## Tier 1 — Green (Auto-allow, no confirmation needed)

These commands are read-only or narrowly scoped writes that cannot cause harm.
Agents may issue them freely.

### Read / Explore
```
ls / ls -la / ls -lah
pwd
cat <file>
head / tail (file inspection only — NEVER to filter mypy/pytest output)
echo
wc -l
file <path>
which <cmd>
```

### Git — Read only
```
git status
git log --oneline / git log --oneline -N
git diff / git diff origin/dev...HEAD
git show <ref>
git branch / git branch -a
git worktree list
git ls-remote origin
git rev-parse <ref>
git fetch origin          ← fetch is always safe (no local changes)
git stash list
git bisect start/bad/good/log/reset   ← full bisect suite for regression hunting
```

### Git — Safe writes (worktree only, not touching dev/main)
```
git checkout -b <feature-branch>    ← creating new branches is safe
git checkout <existing-branch>      ← switching within worktree
git fetch origin && git merge origin/dev   ← syncing from remote dev
git add <files>                     ← staging specific files
git add -A                          ← staging all
git commit -m "..."                 ← committing within worktree
git push origin <feature-branch>    ← pushing non-main branches
git merge origin/dev                ← integrating latest dev into feature branch
git worktree remove --force <path>  ← self-destruct of OWN worktree (end of task)
git worktree prune                  ← pruning stale worktree refs
```

### GitHub CLI — Read only
```
gh auth status
gh repo view
gh pr view <N> --json <fields>
gh pr list --state <state>
gh issue view <N>
gh issue list --search "..."
gh run list / gh run view
```

### GitHub CLI — Safe writes
```
gh pr checkout <N>        ← checkout is safe (own worktree)
gh pr create              ← creating a new PR
gh pr merge <N> --squash --delete-branch   ← ONLY after grade is output
gh issue close <N> --comment "..."        ← ONLY after merge confirmed
gh issue create --title "..." --body "..."
```

### Docker — Inspection
```
docker compose ps
docker compose logs <service>
docker compose exec maestro ls <path>
docker compose exec maestro cat <file>
```

### Docker — Test & type-check execution
```
docker compose exec maestro mypy <paths>
docker compose exec storpheus mypy <paths>
docker compose exec maestro pytest <file> -v
docker compose exec storpheus pytest <file> -v
docker compose exec maestro sh -c "PYTHONPATH=... mypy ..."
docker compose exec maestro sh -c "PYTHONPATH=... pytest ..."
docker compose exec maestro sh -c "export COVERAGE_FILE=... python -m coverage ..."
```

### Search
```
rg / ripgrep (any flags)      ← preferred over grep
grep (read-only search)
find <path> -name "..."       ← read-only find
```

### Filesystem — Safe creates (within worktree only)
```
mkdir -p <path>   ← up to 3 directory levels deep within worktree
cp <src> <dst>    ← within worktree only
mv <src> <dst>    ← within worktree only
```

---

## Tier 2 — Yellow (Review before running — check scope and intent)

These commands can cause harm if misused. Agents must justify why they're
running them and ensure scope is limited.

| Command | When OK | When NOT OK |
|---------|---------|-------------|
| `rm <file>` (single file, no -r) | Removing a temp file the agent created | Removing any tracked file without explicit justification |
| `mkdir` > 3 levels deep | Structured test fixture setup | Arbitrary deep hierarchies |
| `git rebase` | Cleaning up commits before PR (non-force) | Any interactive rebase that rewrites shared history |
| `git merge <branch>` (non-dev) | Merging a sibling feature branch explicitly requested | Merging random branches to resolve an unclear conflict |
| `docker compose build` | `requirements.txt`, `Dockerfile`, or `entrypoint.sh` changed | After code-only changes (bind mounts make this unnecessary) |
| `docker compose up -d` | After a build is confirmed necessary | Restarting to "flush state" without a real reason |
| `docker compose restart` | Service is confirmed unhealthy | Routine — use logs to diagnose first |
| `git cherry-pick` | Moving a specific commit explicitly requested | As a substitute for properly merging dev |
| `pip install` / adding to `requirements.txt` | New dependency with user approval | Without updating `Dockerfile` and flagging the user |
| `gh pr merge` without prior grade output | Never | Always output grade + "Approved for merge" FIRST |
| `curl` (read/GET only) | Probing a local endpoint for diagnostics | Any POST/PUT/DELETE to production |
| `git stash` / `git stash pop` | Temporarily shelving uncommitted work | As a substitute for committing |
| `python` / `python3` on the host | Never — use Docker | Always |

---

## Tier 3 — Red (Never run — forbidden regardless of context)

These commands are forbidden. If an agent believes one is necessary, it must
stop and ask the user, explaining exactly why.

```
# Recursive force-delete of directories (EXCEPT git worktree remove --force, which is whitelisted above)
rm -rf <anything>
rm -r <anything>

# Force-pushing to any branch
git push --force
git push -f
git push origin dev          ← never push directly to dev
git push origin main         ← never push directly to main

# Hard reset that discards commits or working state
git reset --hard origin/dev
git reset --hard HEAD~N      ← unless agent explicitly created all N commits and none are pushed

# Destructive Docker operations
docker system prune
docker volume prune
docker container prune
docker rm -f <container>    ← use restart/stop, not rm

# System-level operations
chmod -R / chmod 777
chown -R
sudo <anything>

# Secrets / credentials exposure
env | grep -i "key\|secret\|token\|password"   ← never log or expose secrets
printenv (if output will be shared)

# Modifying the main repo working tree for test purposes
# (NEVER cd into $REPO and edit files there — that pollutes dev)

# Ending a review without outputting a grade
gh pr merge <N>   ← without having output "Grade: X" and "Approved for merge" above it
```

---

## Hard rules for all agents

1. **Never pipe `mypy` or `pytest` through `grep`, `head`, or `tail`.**
   The exit code is the authoritative signal. Filtering hides failures.

2. **Never run Python on the host.** Always `docker compose exec <service> ...`.

3. **Never copy files into the main repo** (`$REPO`) for testing. Your worktree
   is already live in Docker at `/worktrees/$WTNAME`.

4. **`git worktree remove --force <path>`** is whitelisted — but only for
   removing your **own** worktree at end of task. Never remove another agent's
   worktree.

5. **When in doubt about a command**, stop and ask the user rather than guessing.
   A paused agent is infinitely better than a destructive one.

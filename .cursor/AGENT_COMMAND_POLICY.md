# Agent Command Policy — Tier Classification

This document defines which shell commands agents may run without confirmation,
which require caution, and which are forbidden. Every parallel-agent workflow
reads this policy before executing any command.

> **Why this exists:** With 5–8 agents running concurrently, per-command
> confirmation dialogs create severe friction. This policy eliminates that
> friction for safe commands while preserving human oversight for destructive ones.

---

## How Cursor's Three Tiers Actually Work

Cursor does not have a formal graylist or blacklist. Here is how the three tiers
map to Cursor's actual mechanisms:

| Policy tier | Cursor mechanism | What happens |
|-------------|-----------------|--------------|
| **Green** | Command Allowlist (UI) | Runs immediately, no prompt, outside sandbox |
| **Yellow** | Auto-sandbox (not on allowlist) | Runs automatically in sandbox; if sandbox restriction tripped → one-time prompt |
| **Red** | Agent behavioral rule (not Cursor-enforced) | Agent reads this doc and refuses; sandbox prompts if a violation reaches it |

**Key implication:** The sandbox does not block specific command patterns — it restricts
filesystem access (outside workspace) and network access (controlled by sandbox.json).
Red-tier commands within the workspace are blocked by agent reasoning, not by Cursor.
This is why agents MUST read this document and follow it.

---

## Cursor Configuration (one-time setup)

### External-File Protection — must be OFF

`Cmd+Shift+J` → Cursor Settings → Agents → **External-File Protection → disable**

Parallel agents edit files inside git worktrees at `~/.cursor/worktrees/`, which are
outside the main workspace. With External-File Protection on, every file edit in a
worktree triggers a "Blocked / Allow edits to external files" prompt. Turn it off once.

### Auto-Run Mode

`Cmd+Shift+J` → Cursor Settings → Agents → Auto-Run → **Auto-Run in Sandbox**

This is the correct setting. Green-tier commands are on the allowlist and run
immediately. Yellow-tier commands run in the sandbox without prompting unless
they trip a restriction. Red-tier commands that reach the sandbox will prompt.

### Workspace sandbox.json (already committed)

`.cursor/sandbox.json` grants the sandbox:
- Docker socket access (`/var/run/docker.sock`) — all `docker compose exec` commands
- Worktrees directory (`~/.cursor/worktrees`) — parallel agent workflows
- Full outbound network — GitHub CLI, Docker Hub, OpenRouter, pypi

### Command Allowlist — paste this into the Cursor UI text field

Clear the existing allowlist entirely, then paste the block below into
**Cursor Settings → Agents → Auto-Run → Command Allowlist**.

> **IMPORTANT:** Paste this as a **single unbroken line** — no line breaks.
> Cursor strips newlines and treats them as spaces, merging adjacent entries
> into one broken token. The comma is the only separator Cursor respects.
>
> **Re-paste whenever this file changes.** If agents are still prompted for commands
> that appear in this list, your Cursor settings are out of date — clear and re-paste.
>
> **Backslash continuations trigger prompts.** Cursor treats the continuation line
> (`"PYTHONPATH=..."`) as a separate, unrecognized command. All multi-line shell
> commands in kickoff prompts must be written as single lines.

```
ls, ls -la, ls -lah, ls -l, pwd, cat, head, tail, echo, true, false, wc, file, which, date, basename, dirname, printf, jq, sort, uniq, tr, awk, sed, cut, xargs, tee, rg, grep, find, mkdir, cp, mv, touch, ln -s, cd, sleep, continue, python3 -c, python3 -m, REPO=, WTNAME=, DEV_SHA=, WT=, NUM=, TITLE=, BRANCH=, PRTREES=, WORKTREE=, ENTRY=, FOLLOW_UP_URL=, ISSUE_URL=, GH_REPO=, export GH_REPO=, declare -a, declare -a ISSUES=, declare -a PRS=, for entry in, for NUM in, for WT in, git status, git log, git diff, git show, git branch, git fetch, git fetch origin, git pull, git pull origin, git pull origin dev, git stash list, git worktree list, git ls-remote, git rev-parse, git ls-files, git merge-base, git describe, git cat-file, git config rerere.enabled, git -C, git -C /Users, git worktree list --porcelain, git bisect, git bisect start, git bisect bad, git bisect good, git bisect log, git bisect reset, git checkout -b, git checkout, git add, git add -A, git commit, git merge origin/dev, git merge, git stash, git stash pop, git stash apply, git restore, git restore --staged, git push origin, git push -u origin, git push origin --delete, git worktree add, git worktree add --detach, git worktree remove --force, git worktree prune, docker compose ps, docker compose logs, docker compose config, docker compose -f, docker ps, docker inspect, docker compose exec maestro mypy, docker compose exec maestro pytest, docker compose exec maestro sh -c, docker compose exec maestro python -m coverage, docker compose exec maestro python -c, docker compose exec maestro python3 -m, docker compose exec maestro ps, docker compose exec maestro ls, docker compose exec maestro cat, docker compose exec maestro rg, docker compose exec maestro grep, docker compose exec maestro find, docker compose exec maestro alembic history, docker compose exec maestro alembic current, docker compose exec maestro alembic heads, docker compose exec maestro alembic upgrade head, docker compose exec maestro python3, docker compose exec storpheus mypy, docker compose exec storpheus pytest, docker compose exec storpheus sh -c, docker compose exec storpheus ls, docker compose exec storpheus cat, docker compose exec storpheus rg, docker compose exec storpheus grep, docker compose exec storpheus python3, docker compose exec postgres psql, gh auth status, gh repo view, gh pr view, gh pr list, gh pr diff, gh pr create, gh pr checkout, gh pr merge, gh pr comment, gh pr review, gh issue view, gh issue list, gh issue create, gh issue close, gh issue comment, gh issue edit, gh label list, gh run list, gh run view, gh release list, gh release view, gh api, ps aux, ps -ef, pgrep, nc -z, curl, REPO=$(git, WTNAME=$(basename, DEV_SHA=$(git, WT=$HOME, BRANCH=$(git
```

---

## Tier 1 — Green (On the Allowlist — runs immediately, no prompt)

### Shell — Read / Inspect
```
ls / ls -la / ls -lah / ls -l
pwd
cat <file>
head -n N <file>              ← file inspection ONLY (never to filter test output)
tail -n N <file>              ← file inspection ONLY (never to filter test output)
echo
true                          ← null-op; used as || true for safe error suppression in scripts
false                         ← null-op complement; used in boolean control flow
continue                      ← bash loop control (skip to next iteration); used in for loops
wc / wc -l
file <path>
which <cmd>
date
basename / dirname
printf
jq <filter> <file>            ← JSON parsing
sleep <N>                     ← harmless pause; agents use between push and gh pr create
```

### Shell — Bash control flow (coordinator setup scripts)
```
declare -a ISSUES=(...)       ← array declaration in coordinator setup scripts
declare -a PRS=(...)          ← array declaration in coordinator setup scripts
for entry in "${ARRAY[@]}"    ← iteration in coordinator setup scripts
PRTREES=<path>                ← variable assignment
WORKTREE=<path>               ← variable assignment
ENTRY=<value>                 ← variable assignment
FOLLOW_UP_URL=<value>         ← B-grade follow-up issue URL
ISSUE_URL=<value>             ← captured issue URL in scripts
GH_REPO=cgcardona/maestro     ← hardcoded repo slug — NEVER derive from local path
export GH_REPO=cgcardona/maestro
```

### Shell — Text Processing (read-only transforms)
```
sort / sort -u
uniq
tr
awk '{...}'
sed 's/.../.../'              ← read-only transforms ONLY — never sed -i (in-place edit)
cut -d... -f...
xargs (read-only pipelines)
```

### Search
```
rg <pattern> [flags]          ← ripgrep preferred over grep
grep -r / grep -n / grep -l [flags]
find <path> -name "..." -type f   ← read-only discovery only
```

### Filesystem — Safe Creates (within worktree or /tmp only)
```
mkdir <path>                  ← with or without -p; within worktree or /tmp only
cp <src> <dst>                ← within worktree only
mv <src> <dst>                ← within worktree only
touch <file>                  ← within worktree only
ln -s <target> <link>         ← within worktree only
```

### Temporary Files
```
echo "..." > /tmp/<file>      ← always safe; /tmp is ephemeral
cat /tmp/<file>
rm /tmp/<file>                ← temp file cleanup only (no -r, no -f on real dirs)
```

### Git — Read Only
```
git status
git log [any flags: --oneline, -n N, --graph, --stat, --name-only, --format=...]
git diff [any variant: --staged, origin/dev...HEAD, HEAD~N, specific files]
git show <ref>
git branch / git branch -a / git branch -r
git worktree list
git ls-remote origin
git rev-parse <ref>
git fetch origin              ← fetch is always safe; no local changes
git pull origin dev           ← shorthand for fetch + merge; coordinator pre-flight only
git stash list
git ls-files
git merge-base HEAD origin/dev
git describe [--tags]
git cat-file -t/-p <object>
```

### Git — Bisect (full suite for regression hunting)
```
git bisect start
git bisect bad [<rev>]
git bisect good [<rev>]
git bisect log
git bisect reset
```

### Git — Config (rerere only — limited to this one safe key)
```
git config rerere.enabled true || true   ← coordinator setup only; enables conflict-resolution cache
                                          ← the || true is intentional: when this command runs as
                                          ← part of a multi-statement script block, Cursor sandboxes
                                          ← the whole block, and the sandbox blocks .git/config writes
                                          ← with EPERM. rerere is an optimization (not critical path),
                                          ← so failure must be non-fatal. ALL other git config writes
                                          ← are Yellow tier.
```

### Git — Safe Writes (worktree scope only — never touching dev/main directly)
```
git checkout -b <feature-branch>   ← creating new branches
git checkout <existing-branch>     ← switching within own worktree
git fetch origin && git merge origin/dev   ← syncing from remote dev
git add <files>                    ← staging specific files
git add -A / git add .             ← staging all changes
git commit -m "..."                ← committing within own worktree
git merge origin/dev               ← integrating latest dev into feature branch
git stash                          ← shelving uncommitted work temporarily
git stash pop                      ← restoring stashed work
git stash apply                    ← same as pop but keeps stash entry
git restore <file>                 ← discarding local changes to a tracked file
git restore --staged <file>        ← unstaging staged changes (used in cleanup)
git worktree add --detach <path> <sha>   ← creating detached worktrees
git worktree remove --force <path>       ← removing OWN worktree at end of task
git worktree prune                       ← pruning stale worktree refs
```

### Git — Push (feature branches only)
```
git push origin <feature-branch>          ← non-main, non-dev branches ONLY
git push -u origin <feature-branch>       ← same as above with upstream tracking; -u writes to .git/config
                                           ← must be on the allowlist so it runs outside sandbox
git push origin --delete <feature-branch> ← remote branch cleanup after PR merge
```

### GitHub CLI — Read Only
```
gh auth status
gh repo view
gh pr view <N> [--json <fields>]
gh pr list [--state <state>] [--json <fields>]
gh pr diff <N>
gh issue view <N>
gh issue list [--search "..."] [--state ...] [--label ...]
gh label list [--limit N]         ← enumerate valid repo labels before gh issue edit
gh run list
gh run view <id>
gh release list
gh release view [<tag>]
gh api <GET-endpoint>              ← read-only API calls only
```

### GitHub CLI — Safe Writes
```
gh pr checkout <N>                 ← checkout into own worktree
gh pr create --title "..." --body "..."
gh pr merge <N> --squash                   ← ONLY after "Grade: X / Approved" output; never --delete-branch (breaks with multi-worktree)
gh pr comment <N> --body "..."
gh pr review <N> [--approve | --request-changes | --comment]
gh issue create --title "..." --body "..."  ← never pass --label here (see note below)
gh issue close <N> [--comment "..."]       ← ONLY after merge confirmed
gh issue comment <N> --body "..."
gh issue edit <N> --add-label "..."        ← apply labels here, after creation, with || true
# ⚠️  LABEL RULE: a single missing label causes gh issue create to fail entirely.
#    Always create the issue first, then apply labels with gh issue edit || true.
#    Valid labels: bug enhancement documentation performance ai-pipeline muse muse-cli
#                 muse-hub muse-music-extensions storpheus maestro-integration mypy
#                 cli testing multimodal help-wanted good-first-issue weekend-mvp
```

### Docker — Inspection
```
docker compose ps
docker compose -f <file> ps   ← explicit compose file; used by coordinator pre-flight
docker compose logs <service>
docker compose config
docker ps
docker inspect <container>
```

### Docker — exec maestro (tests, type checks, migrations, inspection)
```
docker compose exec maestro mypy <paths>
docker compose exec maestro pytest <file> -v [flags]
docker compose exec maestro sh -c "<any command>"
docker compose exec maestro python -m coverage run ...
docker compose exec maestro python -m coverage report ...
docker compose exec maestro python3 -m <module>       ← stdlib modules inside container
docker compose exec maestro ps aux                    ← process inspection inside container
docker compose exec maestro ls <path>
docker compose exec maestro cat <file>
docker compose exec maestro rg <pattern>
docker compose exec maestro grep <pattern>
docker compose exec maestro find <path>
docker compose exec maestro python -c "<one-liner>"   ← Python one-liner inside container
docker compose exec maestro python3 <script>          ← explicit python3 binary
docker compose exec maestro alembic history
docker compose exec maestro alembic current
docker compose exec maestro alembic heads
docker compose exec maestro alembic upgrade head   ← forward migration only
```

### Docker — exec storpheus
```
docker compose exec storpheus mypy <paths>
docker compose exec storpheus pytest <file> -v [flags]
docker compose exec storpheus sh -c "<any command>"
docker compose exec storpheus ls <path>
docker compose exec storpheus cat <file>
docker compose exec storpheus rg <pattern>
docker compose exec storpheus grep <pattern>
```

### Docker — exec postgres (read-only queries only)
```
docker compose exec postgres psql -U maestro -c "SELECT ..."
docker compose exec postgres psql -U maestro -c "\dt"
docker compose exec postgres psql -U maestro -c "\d <table>"
```

### Process Inspection
```
ps aux
ps -ef
pgrep <name>
```

### Python — Host-side one-liners (read-only data processing only)
```
python3 -c "<one-liner>"      ← JSON parsing, string manipulation of gh/git output ONLY
                               ← No file writes, no imports beyond stdlib, no network calls
                               ← Longer scripts or any I/O must use docker compose exec
python3 -m json.tool           ← pretty-printing JSON output from curl or gh commands
python3 -m <stdlib-module>     ← stdlib modules only (json, base64, hashlib, etc.)
```

### Network — Read-only probes
```
curl -s <local-url>            ← GET only; local health checks only (e.g. localhost:10001/health)
nc -z <host> <port>            ← port connectivity check only
```

---

## Tier 2 — Yellow (NOT on Allowlist — runs in sandbox, may prompt once)

These commands are NOT in the Cursor allowlist. They run in the sandbox automatically
but will prompt if they trip a sandbox restriction. Agents should justify why they're
needed and confirm scope is correct before running.

| Command | When OK | When NOT OK |
|---------|---------|-------------|
| `rm <single-file>` | Removing a specific tracked file the agent is deleting as part of its task | Any file not created or explicitly owned by this task |
| `docker compose build <service>` | `requirements.txt`, `Dockerfile`, or `entrypoint.sh` changed | After code-only changes — bind mounts make this unnecessary |
| `docker compose up -d` | After a confirmed necessary rebuild | Restarting to "flush state" without a real reason |
| `docker compose restart <service>` | Service is confirmed unhealthy after checking logs | Routine troubleshooting — use logs to diagnose first |
| `git cherry-pick <sha>` | Moving a specific commit explicitly requested by user | As a substitute for properly merging dev |
| `git rebase <branch>` | Cleaning up local commits before PR (own worktree, not shared) | Any rebase that rewrites shared/pushed history |
| `git stash drop` | Discarding a stash explicitly created by this agent | Any stash not created by this agent |
| `curl -s <url>` POST/PUT | Probing a local endpoint for a diagnostic reason | Any POST/PUT/DELETE to production or external services |
| `docker compose exec maestro alembic downgrade` | Explicitly requested DB rollback | Routine use — destructive, should always be questioned |
| `docker compose exec postgres psql` with mutations | Explicitly requested data fix | Any ad-hoc data modification without explicit user request |
| `sed -i` (in-place file edit via shell) | Never — use the StrReplace tool instead | Always |
| `python3 <script.py>` on the host | Never — use `docker compose exec` | Always |
| `python3 -c "..."` with file I/O or non-stdlib | Use `docker compose exec` instead | When the one-liner writes files, imports non-stdlib, or makes network calls |

---

## Tier 3 — Red (Never run — regardless of context or seeming necessity)

These commands are **forbidden**. If an agent believes one is necessary, it must
stop, explain why to the user, and wait for explicit approval. A paused agent
is infinitely better than a destructive one.

### Recursive deletion
```
rm -rf <anything>
rm -r <anything>
rmdir -p <anything>
```

### Force-pushing or pushing to protected branches
```
git push --force
git push -f
git push --force-with-lease     ← still forbidden without explicit user approval
git push origin dev             ← NEVER push directly to dev
git push origin main            ← NEVER push directly to main
```

### Hard reset (discards history / working state)
```
git reset --hard <anything>
git clean -fd                   ← deletes untracked files
git clean -fdx                  ← deletes untracked + ignored files
```

### Destructive Docker operations
```
docker system prune
docker volume prune
docker container prune
docker image prune
docker rm -f <container>        ← use restart/stop, not rm
docker volume rm <volume>
docker compose down -v          ← -v flag destroys named volumes (including Postgres data)
```

### System-level operations
```
chmod -R <anything>
chmod 777 <anything>
chown -R <anything>
sudo <anything>
su <anything>
```

### Secrets exposure
```
env | grep -i "key\|secret\|token\|password"
printenv
cat .env
cat .muse/config.toml           ← contains Hub auth token
```

### Alembic destructive
```
docker compose exec maestro alembic downgrade base   ← drops all tables
alembic stamp <rev>                                  ← rewrites migration pointer without migrating
```

### Production / external mutations
```
curl -X POST/PUT/DELETE <external-url>
gh release create               ← creating releases requires explicit user intent
npm publish / pip publish       ← publishing packages
```

### Polluting the main repo working tree
```
# NEVER cd into $REPO and edit files there — that pollutes dev
# Your worktree is your sandbox — stay in it
```

### Merging without grade output
```
gh pr merge <N>   ← without having first output "Grade: X" and "Approved for merge"
```

---

## Hard Rules (apply regardless of tier)

1. **Never pipe `mypy` or `pytest` output through `grep`, `head`, or `tail`.**
   The exit code is the authoritative signal. Filtering hides failures.

2. **Never run Python scripts on the host.** `python3 -c "..."` one-liners are allowed for
   read-only stdlib-only data processing (e.g. JSON parsing of `gh` output). Any script file,
   file writes, non-stdlib imports, or network calls must use `docker compose exec <service> ...`.

3. **Never `sed -i` to edit files.** Use the StrReplace tool — it's safer, tracked, and shows a diff.

4. **Never copy files into the main repo** (`$REPO`) for testing.
   Your worktree is already live inside Docker via bind-mount.

5. **`git worktree remove --force <path>`** is allowed — but ONLY for your OWN worktree,
   at the end of your task. Never remove another agent's worktree.

6. **When in doubt, stop and ask.** Explain exactly what you need and why.
   A paused agent is infinitely better than a destructive one.

7. **Never use `while read` in pipelines for issue/PR data.** Use `xargs` instead — it is on
   the allowlist and avoids the sandbox prompt that `read` (a shell builtin) triggers.
   ```bash
   # ✅ Correct — uses xargs (allowlisted, no prompt):
   gh pr view <N> --json body --jq '.body' \
     | grep -oE '[Cc]loses?\s+#[0-9]+' \
     | grep -oE '[0-9]+' \
     | xargs -I{} gh issue close {} --comment "Fixed by PR #<N>." --repo "$GH_REPO"

   # ❌ Wrong — while read triggers a sandbox prompt:
   | while read ISSUE_NUM; do gh issue close "$ISSUE_NUM" ...; done
   ```

8. **`GH_REPO` is always `cgcardona/maestro` — hardcoded, never derived.**
   The local path (`/Users/gabriel/dev/tellurstori/maestro`) contains `tellurstori` which is
   NOT the GitHub org. Using `gh` without `--repo "$GH_REPO"` or with a derived slug causes
   "Forbidden" / "Repository not found" errors.
   ```bash
   export GH_REPO=cgcardona/maestro   # always set this first; all gh commands inherit it
   ```

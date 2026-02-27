#!/usr/bin/env bash
# worktree-clean.sh — remove Cursor worktrees whose branches are merged into dev
#
# Usage (from anywhere in the repo):
#   scripts/worktree-clean.sh
#
# What it does:
#   1. Fetches origin so merge status is current
#   2. Finds every worktree (except the main one) on a branch already merged into origin/dev
#   3. Force-removes the worktree directory
#   4. Deletes the local branch
#   5. Reports what was cleaned and what was skipped

set -euo pipefail

REPO_ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
cd "$REPO_ROOT"

echo "🔍 Fetching origin..."
git fetch origin --prune --quiet

echo ""
echo "🌳 Current worktrees:"
git worktree list
echo ""

CLEANED=0
SKIPPED=0

while IFS= read -r line; do
  WT_PATH=$(echo "$line" | awk '{print $1}')
  BRANCH=$(echo "$line" | sed -n 's/.*\[\(.*\)\]/\1/p')

  # Skip main worktree, bare repos, detached HEADs, and protected branches
  [[ "$WT_PATH" == "$REPO_ROOT" ]] && continue
  [[ -z "$BRANCH" ]] && continue
  [[ "$BRANCH" == "dev" || "$BRANCH" == "main" ]] && continue

  # Is the branch fully merged into origin/dev?
  MERGED=false
  if git merge-base --is-ancestor "refs/remotes/origin/$BRANCH" "refs/remotes/origin/dev" 2>/dev/null; then
    MERGED=true
  elif git show-ref --verify --quiet "refs/heads/$BRANCH" && \
       git merge-base --is-ancestor "refs/heads/$BRANCH" "refs/remotes/origin/dev" 2>/dev/null; then
    MERGED=true
  fi

  if [[ "$MERGED" == "true" ]]; then
    echo "✅  Cleaning merged branch: $BRANCH"
    echo "    Worktree: $WT_PATH"
    git worktree remove --force "$WT_PATH" 2>/dev/null && echo "    🗑  Worktree removed" || echo "    ℹ️  Worktree already gone"
    git branch -D "$BRANCH" 2>/dev/null && echo "    🗑  Local branch deleted" || echo "    ℹ️  Branch already gone"
    CLEANED=$((CLEANED + 1))
  else
    echo "⏭  Skipping (still active): $BRANCH"
    SKIPPED=$((SKIPPED + 1))
  fi
  echo ""
done < <(git worktree list)

echo "────────────────────────────────────"
echo "Cleaned: $CLEANED  |  Skipped: $SKIPPED"
echo ""
echo "🌳 Remaining worktrees:"
git worktree list

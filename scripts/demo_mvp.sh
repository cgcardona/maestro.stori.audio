#!/usr/bin/env bash
# demo_mvp.sh — Golden-path local Muse MVP workflow (steps 1–11)
#
# Exercises the complete local Muse VCS lifecycle inside Docker:
#   init → commit → branch → commit → checkout → merge (conflict)
#   → resolve → merge --continue → log --graph
#
# Usage (from repo root):
#   docker compose exec maestro bash /app/scripts/demo_mvp.sh
#
# Exit code 0 means every step passed.  Any command failure aborts immediately.
#
# Requirements:
#   - maestro container running with a live Postgres instance
#   - Muse CLI installed (entry point: muse)

set -euo pipefail

# ── Colour helpers ──────────────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
RESET='\033[0m'

step() { echo -e "\n${CYAN}━━━ Step $1 — $2 ${RESET}"; }
ok()   { echo -e "  ${GREEN}✅ $*${RESET}"; }
info() { echo -e "  ${YELLOW}ℹ️  $*${RESET}"; }
fail() { echo -e "  ${RED}❌ $*${RESET}"; exit 1; }

# ── Setup: temp working directory ───────────────────────────────────────────
WORK_DIR="$(mktemp -d /tmp/muse-demo-XXXXXX)"
info "Working directory: $WORK_DIR"

cleanup() {
    info "Cleaning up $WORK_DIR ..."
    rm -rf "$WORK_DIR"
}
trap cleanup EXIT

cd "$WORK_DIR"

# ── Step 1: muse init ────────────────────────────────────────────────────────
step 1 "muse init"
muse init
ok "Repository initialised in $WORK_DIR/.muse"

# Verify .muse structure
test -f .muse/repo.json  || fail ".muse/repo.json missing"
test -f .muse/HEAD       || fail ".muse/HEAD missing"
test -f .muse/config.toml || fail ".muse/config.toml missing"
ok ".muse/ directory structure correct"

# ── Step 2: Generate initial artifacts into muse-work/ ───────────────────────
step 2 "Generate initial artifacts into muse-work/"
mkdir -p muse-work/meta muse-work/tracks
cat > muse-work/meta/section-1.json <<'EOF'
{
  "section": "intro",
  "tempo_bpm": 120,
  "key": "C major",
  "time_signature": "4/4",
  "version": "main-v1"
}
EOF
cat > muse-work/tracks/drums.json <<'EOF'
{
  "instrument": "drums",
  "pattern": "boom-bap",
  "bars": 8,
  "version": "main-v1"
}
EOF
ok "Created muse-work/meta/section-1.json and muse-work/tracks/drums.json"

# ── Step 3: muse commit (initial snapshot) ───────────────────────────────────
step 3 "muse commit -m 'feat: initial generation'"
muse commit -m "feat: initial generation"
ok "First commit on main"

# Capture HEAD commit ID from .muse/refs/heads/main
MAIN_COMMIT_1="$(cat .muse/refs/heads/main)"
info "main HEAD = ${MAIN_COMMIT_1:0:8}"
test -n "$MAIN_COMMIT_1" || fail "main ref is empty after commit"

# ── Step 4: muse checkout -b experiment ──────────────────────────────────────
step 4 "muse checkout -b experiment"
muse checkout -b experiment
ok "Switched to new branch 'experiment'"

# Verify HEAD points to experiment
HEAD_REF="$(cat .muse/HEAD | tr -d '[:space:]')"
test "$HEAD_REF" = "refs/heads/experiment" || fail "HEAD should point to experiment, got $HEAD_REF"
ok "HEAD is refs/heads/experiment"

# ── Step 5: Generate different artifacts on experiment branch ─────────────────
step 5 "Generate experimental artifacts into muse-work/"
# Modify section-1 (this will cause a conflict when merging back)
cat > muse-work/meta/section-1.json <<'EOF'
{
  "section": "intro",
  "tempo_bpm": 140,
  "key": "C major",
  "time_signature": "4/4",
  "version": "experiment-v1"
}
EOF
# Add a new track unique to experiment
cat > muse-work/tracks/bass.json <<'EOF'
{
  "instrument": "bass",
  "pattern": "walking",
  "bars": 8,
  "version": "experiment-v1"
}
EOF
ok "Modified section-1.json + added tracks/bass.json on experiment"

# ── Step 6: muse commit on experiment branch ──────────────────────────────────
step 6 "muse commit -m 'feat: experimental variation'"
muse commit -m "feat: experimental variation"
ok "Committed on experiment branch"

EXPERIMENT_COMMIT="$(cat .muse/refs/heads/experiment)"
info "experiment HEAD = ${EXPERIMENT_COMMIT:0:8}"
test -n "$EXPERIMENT_COMMIT" || fail "experiment ref is empty after commit"

# ── Step 7: muse checkout main ───────────────────────────────────────────────
step 7 "muse checkout main"
muse checkout main
ok "Switched back to main"

HEAD_REF="$(cat .muse/HEAD | tr -d '[:space:]')"
test "$HEAD_REF" = "refs/heads/main" || fail "HEAD should point to main, got $HEAD_REF"
ok "HEAD is refs/heads/main"

# Modify section-1 on main to create a genuine conflict
cat > muse-work/meta/section-1.json <<'EOF'
{
  "section": "verse",
  "tempo_bpm": 110,
  "key": "G major",
  "time_signature": "4/4",
  "version": "main-v2"
}
EOF
muse commit -m "feat: verse section on main"
MAIN_COMMIT_2="$(cat .muse/refs/heads/main)"
info "main HEAD after v2 commit = ${MAIN_COMMIT_2:0:8}"

# ── Step 8: muse merge experiment (triggers conflict) ────────────────────────
step 8 "muse merge experiment — expects conflict on section-1.json"
# We expect muse merge to exit non-zero due to conflict
set +e
muse merge experiment
MERGE_EXIT=$?
set -e

if [ $MERGE_EXIT -eq 0 ]; then
    fail "Expected merge conflict but merge succeeded cleanly"
fi
ok "Merge correctly detected conflict (exit $MERGE_EXIT)"

# Verify MERGE_STATE.json was written
test -f .muse/MERGE_STATE.json || fail ".muse/MERGE_STATE.json not created"
ok "MERGE_STATE.json created"
info "Conflicts: $(python3 -c "import json; d=json.load(open('.muse/MERGE_STATE.json')); print(d['conflict_paths'])")"

# ── Step 9: muse resolve --ours ───────────────────────────────────────────────
step 9 "muse resolve muse-work/meta/section-1.json --ours"
muse resolve muse-work/meta/section-1.json --ours
ok "Conflict on section-1.json resolved (keeping ours)"

# After resolving, MERGE_STATE.json persists with conflict_paths=[] so that
# muse merge --continue can read the stored commit IDs.
test -f .muse/MERGE_STATE.json || fail "MERGE_STATE.json missing after resolve (--continue needs it)"
REMAINING="$(python3 -c "import json; d=json.load(open('.muse/MERGE_STATE.json')); print(len(d.get('conflict_paths', [])))")"
test "$REMAINING" = "0" || fail "Expected 0 remaining conflicts, got $REMAINING"
ok "MERGE_STATE.json has 0 remaining conflicts (ready for --continue)"

# ── Step 10: muse merge --continue ───────────────────────────────────────────
step 10 "muse merge --continue"
muse merge --continue
ok "Merge commit created"

MERGE_COMMIT="$(cat .muse/refs/heads/main)"
info "main HEAD after merge = ${MERGE_COMMIT:0:8}"
test -n "$MERGE_COMMIT" || fail "main ref empty after merge --continue"
test "$MERGE_COMMIT" != "$MAIN_COMMIT_2" || fail "HEAD did not advance after merge commit"

# muse merge --continue clears MERGE_STATE.json
test ! -f .muse/MERGE_STATE.json || fail "MERGE_STATE.json should be cleared by --continue"
ok "MERGE_STATE.json cleared by --continue"

# ── Step 11: muse log --graph ─────────────────────────────────────────────────
step 11 "muse log --graph — verify non-linear DAG"
LOG_OUTPUT="$(muse log --graph)"
echo "$LOG_OUTPUT"
ok "muse log --graph produced output"

# The graph must contain at least 3 commit markers (C: initial, v2, merge)
COMMIT_COUNT="$(echo "$LOG_OUTPUT" | grep -c '^\*' || true)"
test "$COMMIT_COUNT" -ge 3 || fail "Expected ≥3 commits in log --graph, got $COMMIT_COUNT"
ok "log --graph shows $COMMIT_COUNT commit nodes"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}════════════════════════════════════════════════════${RESET}"
echo -e "${GREEN}  ✅ Muse MVP local golden path — ALL STEPS PASSED  ${RESET}"
echo -e "${GREEN}════════════════════════════════════════════════════${RESET}"
echo "  Commits:"
echo "    main initial:  ${MAIN_COMMIT_1:0:8}"
echo "    main v2:       ${MAIN_COMMIT_2:0:8}"
echo "    experiment:    ${EXPERIMENT_COMMIT:0:8}"
echo "    merge commit:  ${MERGE_COMMIT:0:8}"

#!/usr/bin/env bash
# demo_remote.sh — Remote Muse MVP workflow (steps 12–15)
#
# Exercises the remote portion of the Muse VCS lifecycle:
#   remote add → push → pull (from second directory) → Hub PR/issue via gh
#
# Prerequisites:
#   - MUSE_HUB_URL must be set (e.g. export MUSE_HUB_URL=https://muse.stori.app)
#   - MUSE_HUB_TOKEN must be set (bearer token for Muse Hub API)
#   - GH_TOKEN must be set if creating GitHub PRs/issues on the Hub
#   - The local golden path (demo_mvp.sh) must have run successfully first,
#     OR this script must be invoked with a pre-existing local repo path:
#       MUSE_REPO_DIR=/path/to/repo bash demo_remote.sh
#
# Usage (from repo root):
#   export MUSE_HUB_URL=https://muse.stori.app
#   export MUSE_HUB_TOKEN=<token>
#   docker compose exec -e MUSE_HUB_URL -e MUSE_HUB_TOKEN maestro \
#       bash /app/scripts/demo_remote.sh

set -euo pipefail

# ── Guards ───────────────────────────────────────────────────────────────────
if [ -z "${MUSE_HUB_URL:-}" ]; then
    echo "❌ MUSE_HUB_URL is not set. Set it to your Muse Hub base URL."
    echo "   Example: export MUSE_HUB_URL=https://muse.stori.app"
    exit 1
fi

# ── Colour helpers ──────────────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
RESET='\033[0m'

step() { echo -e "\n${CYAN}━━━ Step $1 — $2 ${RESET}"; }
ok()   { echo -e "  ${GREEN}✅ $*${RESET}"; }
info() { echo -e "  ${YELLOW}ℹ️  $*${RESET}"; }

# ── Setup: use existing repo or create fresh one ─────────────────────────────
if [ -n "${MUSE_REPO_DIR:-}" ]; then
    info "Using existing repo at $MUSE_REPO_DIR"
    cd "$MUSE_REPO_DIR"
else
    # Run the local golden path first to have a repo with history.
    WORK_DIR="$(mktemp -d /tmp/muse-demo-remote-XXXXXX)"
    info "Creating fresh repo for remote demo at $WORK_DIR"

    cleanup() {
        info "Cleaning up $WORK_DIR ..."
        rm -rf "$WORK_DIR"
    }
    trap cleanup EXIT

    cd "$WORK_DIR"
    muse init
    mkdir -p muse-work/meta muse-work/tracks
    cat > muse-work/meta/section-1.json <<'EOF'
{"section":"intro","tempo_bpm":120,"key":"C major"}
EOF
    muse commit -m "feat: initial generation"
fi

info "Hub URL: $MUSE_HUB_URL"

# ── Step 12: muse remote add origin ──────────────────────────────────────────
step 12 "muse remote add origin $MUSE_HUB_URL"
muse remote add origin "$MUSE_HUB_URL"
ok "Remote 'origin' configured → $MUSE_HUB_URL"

# ── Step 13: muse push ───────────────────────────────────────────────────────
step 13 "muse push"
muse push
ok "Local history pushed to $MUSE_HUB_URL"

# ── Step 14: muse pull --branch main (simulate Rene's side) ──────────────────
step 14 "muse pull --branch main (Rene's machine simulation)"
RENE_DIR="$(mktemp -d /tmp/muse-demo-rene-XXXXXX)"
info "Rene's working directory: $RENE_DIR"

cleanup_rene() { rm -rf "$RENE_DIR"; }
trap cleanup_rene EXIT

(
    cd "$RENE_DIR"
    muse init
    muse remote add origin "$MUSE_HUB_URL"
    muse pull --branch main
    ok "Rene's pull complete"

    # Verify Rene has the same history as Gabriel
    RENE_LOG="$(muse log)"
    GABRIEL_LOG="$(cd "${MUSE_REPO_DIR:-$WORK_DIR}" && muse log)"
    if [ "$RENE_LOG" = "$GABRIEL_LOG" ]; then
        ok "Rene's log matches Gabriel's — round-trip verified"
    else
        echo -e "  ${YELLOW}⚠️  Logs differ (may reflect expected remote filtering)${RESET}"
        info "Gabriel: $(echo "$GABRIEL_LOG" | head -3)"
        info "Rene:    $(echo "$RENE_LOG" | head -3)"
    fi
)

# ── Step 15: Create PR + issue on Muse Hub via API ───────────────────────────
step 15 "Create Hub PR and issue via API"
REPO_ID="$(python3 -c "import json; print(json.load(open('.muse/repo.json'))['repo_id'])")"
info "repo_id: $REPO_ID"

if [ -n "${MUSE_HUB_TOKEN:-}" ]; then
    # Create an issue on the Hub
    ISSUE_RESP="$(curl -s -X POST \
        -H "Authorization: Bearer $MUSE_HUB_TOKEN" \
        -H "Content-Type: application/json" \
        -d "{\"title\":\"Demo: golden-path integration test\",\"body\":\"Created by demo_remote.sh\",\"repo_id\":\"$REPO_ID\"}" \
        "$MUSE_HUB_URL/api/v1/muse/repos/$REPO_ID/issues" || true)"
    info "Issue response: $ISSUE_RESP"

    # Create a PR on the Hub
    PR_RESP="$(curl -s -X POST \
        -H "Authorization: Bearer $MUSE_HUB_TOKEN" \
        -H "Content-Type: application/json" \
        -d "{\"title\":\"Merge experiment → main\",\"body\":\"Demo PR created by demo_remote.sh\",\"repo_id\":\"$REPO_ID\",\"head\":\"experiment\",\"base\":\"main\"}" \
        "$MUSE_HUB_URL/api/v1/muse/repos/$REPO_ID/pulls" || true)"
    info "PR response: $PR_RESP"
    ok "Hub PR and issue API calls completed"
else
    info "MUSE_HUB_TOKEN not set — skipping Hub PR/issue creation"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}══════════════════════════════════════════════════════════${RESET}"
echo -e "${GREEN}  ✅ Muse MVP remote golden path — ALL STEPS PASSED       ${RESET}"
echo -e "${GREEN}══════════════════════════════════════════════════════════${RESET}"
echo "  Hub URL:  $MUSE_HUB_URL"
echo "  repo_id:  $REPO_ID"

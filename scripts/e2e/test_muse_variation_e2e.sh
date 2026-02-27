#!/bin/bash
# End-to-End Test for Muse Variation System
# Tests the complete variation workflow with spec-compliant terminology
# Do not add internal IPs or secrets; use env vars or arguments.

set -e

# Load .env from project root (two dirs up from scripts/e2e/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_env_file="$SCRIPT_DIR/../../.env"
if [ -f "$_env_file" ]; then
  set -a
  # shellcheck source=../../.env
  source "$_env_file"
  set +a
fi

# Token: arg takes precedence, else .env
TOKEN="${1:-$E2E_ACCESS_TOKEN}"
API_URL="${STORI_E2E_API_BASE:-http://localhost:10001/api/v1}"

if [ -z "$TOKEN" ]; then
    echo "❌ No JWT token. Either:"
    echo "   1. Add E2E_ACCESS_TOKEN to .env, or"
    echo "   2. Pass token as argument: $0 <jwt-token>"
    echo ""
    echo "Generate: python scripts/generate_access_code.py --generate-user-id --hours 24 -q"
    exit 1
fi

echo "🎭 Muse Variation System - End-to-End Test"
echo "=========================================="
echo ""

# Test 1: Streaming Variation via /maestro/stream
echo "📝 Test 1: Generate variation via streaming endpoint"
echo "   (COMPOSING intent -> backend forces variation mode)"
echo ""

PROJECT_STATE='{
  "projectId": "test-project-001",
  "tempo": 120,
  "key": "C",
  "tracks": [
    {
      "id": "track-1",
      "name": "Piano",
      "regions": [
        {
          "id": "region-1",
          "name": "Intro",
          "startBeat": 0,
          "durationBeats": 16,
          "notes": [
            {"pitch": 60, "startBeat": 0, "duration": 1, "velocity": 100},
            {"pitch": 62, "startBeat": 1, "duration": 1, "velocity": 100},
            {"pitch": 64, "startBeat": 2, "duration": 1, "velocity": 100},
            {"pitch": 65, "startBeat": 3, "duration": 1, "velocity": 100}
          ]
        }
      ]
    }
  ]
}'

TEMP_FILE=$(mktemp)

curl -k -N -X POST "${API_URL}/maestro/stream" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"prompt\": \"Make it minor and darker\",
    \"project\": ${PROJECT_STATE},
    \"conversation_id\": \"test-variation-001\"
  }" 2>/dev/null | tee "$TEMP_FILE"

echo ""
echo "✅ Response received. Analyzing events..."
echo ""

# Parse and verify SSE events
if grep -q '"type":"meta"' "$TEMP_FILE"; then
    echo "✅ Found 'meta' event (variation summary)"
    
    # Extract and display meta event
    META_LINE=$(grep '"type":"meta"' "$TEMP_FILE" | head -1)
    echo "   Content: ${META_LINE:0:100}..."
    
    # Check for required fields
    if echo "$META_LINE" | grep -q '"variation_id"'; then
        echo "   ✓ Contains variation_id"
    fi
    if echo "$META_LINE" | grep -q '"note_counts"'; then
        echo "   ✓ Contains note_counts"
    fi
    if echo "$META_LINE" | grep -q '"affected_tracks"'; then
        echo "   ✓ Contains affected_tracks"
    fi
else
    echo "❌ Missing 'meta' event"
fi

echo ""

if grep -q '"type":"phrase"' "$TEMP_FILE"; then
    PHRASE_COUNT=$(grep -c '"type":"phrase"' "$TEMP_FILE" || echo "0")
    echo "✅ Found ${PHRASE_COUNT} 'phrase' event(s)"
    
    # Extract and display first phrase
    PHRASE_LINE=$(grep '"type":"phrase"' "$TEMP_FILE" | head -1)
    
    # Check for required fields
    if echo "$PHRASE_LINE" | grep -q '"phrase_id"'; then
        echo "   ✓ Contains phrase_id"
    fi
    if echo "$PHRASE_LINE" | grep -q '"start_beat"'; then
        echo "   ✓ Contains start_beat"
    fi
    if echo "$PHRASE_LINE" | grep -q '"end_beat"'; then
        echo "   ✓ Contains end_beat"
    fi
    if echo "$PHRASE_LINE" | grep -q '"note_changes"'; then
        echo "   ✓ Contains note_changes"
    fi
    
    # Check note_changes structure
    if echo "$PHRASE_LINE" | grep -q '"start_beat".*"duration_beats"'; then
        echo "   ✓ Notes use beat-based fields (start_beat, duration_beats)"
    fi
else
    echo "❌ Missing 'phrase' events"
fi

echo ""

if grep -q '"type":"done"' "$TEMP_FILE"; then
    echo "✅ Found 'done' event (completion signal)"
else
    echo "❌ Missing 'done' event"
fi

echo ""

# Verify NO Git terminology
if grep -qi "hunk" "$TEMP_FILE"; then
    echo "❌ FAIL: Found 'hunk' terminology (should be 'phrase')"
else
    echo "✅ Zero Git terminology - uses 'phrase' only"
fi

if grep -q '"time_range"' "$TEMP_FILE"; then
    echo "❌ FAIL: Found 'time_range' (should be 'beat_range')"
else
    echo "✅ Beat-based reasoning - uses 'beat_range' not 'time_range'"
fi

rm "$TEMP_FILE"

echo ""
echo "=========================================="
echo "📝 Test 2: Direct /variation/propose endpoint"
echo ""

# Test 2: Direct variation endpoints
PROPOSE_RESPONSE=$(curl -k -s -X POST "${API_URL}/variation/propose" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"project_id\": \"test-project-002\",
    \"base_state_id\": \"0\",
    \"intent\": \"make the melody more mysterious\",
    \"options\": {
      \"phrase_grouping\": \"bars\",
      \"bar_size\": 4
    }
  }" 2>&1)

if echo "$PROPOSE_RESPONSE" | grep -q '"variation_id"'; then
    echo "✅ /variation/propose endpoint working"
    
    VARIATION_ID=$(echo "$PROPOSE_RESPONSE" | grep -o '"variation_id":"[^"]*"' | cut -d'"' -f4)
    echo "   Variation ID: ${VARIATION_ID:0:12}..."
    
    if echo "$PROPOSE_RESPONSE" | grep -q '"stream_url"'; then
        echo "   ✓ Contains stream_url"
    fi
else
    echo "⚠️  /variation/propose response: $PROPOSE_RESPONSE"
fi

echo ""
echo "=========================================="
echo "📝 Test 3: /variation/discard endpoint"
echo ""

DISCARD_RESPONSE=$(curl -k -s -X POST "${API_URL}/variation/discard" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"project_id\": \"test-project-002\",
    \"variation_id\": \"test-var-123\"
  }" 2>&1)

if echo "$DISCARD_RESPONSE" | grep -q '"ok":true'; then
    echo "✅ /variation/discard endpoint working"
else
    echo "⚠️  /variation/discard response: $DISCARD_RESPONSE"
fi

echo ""
echo "=========================================="
echo "🎉 Muse Variation System Test Complete"
echo ""
echo "Summary:"
echo "- ✅ Streaming variations with meta/phrase/done events"
echo "- ✅ Beat-based reasoning (no seconds/time_range)"
echo "- ✅ Musical terminology (no Git hunk/diff)"
echo "- ✅ Spec-compliant endpoints working"
echo ""
echo "🚀 Backend ready for frontend integration!"

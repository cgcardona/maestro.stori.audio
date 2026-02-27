#!/bin/bash
# STRESS TEST SUITE - Push Stori to its limits
# Note: We DON'T use set -e so tests continue even if some fail
#
# Test Design:
# - Mix of comma-separated and space-separated instrument lists (both should work)
# - Various genres, tempos, and complexity levels
# - Tests with 1-16+ instruments
# - Tests with heavy effects/mixing
# - Long form compositions (up to 32 bars)

# Load .env from project root (two dirs up from scripts/e2e/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_env_file="$SCRIPT_DIR/../../.env"
if [ -f "$_env_file" ]; then
  set -a
  # shellcheck source=../../.env
  source "$_env_file"
  set +a
fi

if [ -z "${E2E_ACCESS_TOKEN:-}" ]; then
  echo "❌ E2E_ACCESS_TOKEN is not set. Add it to .env (generate with: python scripts/generate_access_code.py --generate-user-id --hours 24 -q)"
  exit 1
fi

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
NC='\033[0m'

TOKEN="$E2E_ACCESS_TOKEN"
MAESTRO_IP="${MAESTRO_IP:-172.18.0.8}"
BASE_URL="http://$MAESTRO_IP:10001/api/v1"

success_count=0
fail_count=0
total_time=0

test_complex() {
    local test_name="$1"
    local prompt="$2"
    local timeout_sec="${3:-60}"
    local project_id="stress-$(date +%s)-$RANDOM"
    
    echo -e "\n${MAGENTA}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${MAGENTA}STRESS TEST: $test_name${NC}"
    echo -e "${BLUE}Prompt: \"$prompt\"${NC}"
    echo -e "${BLUE}Timeout: ${timeout_sec}s${NC}"
    echo -e "${MAGENTA}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    
    local start_time=$(date +%s)
    
    local output=$(timeout ${timeout_sec}s curl -s -N -X POST "$BASE_URL/maestro/stream" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer $TOKEN" \
        -d "{\"prompt\":\"$prompt\",\"project\":{\"projectId\":\"$project_id\",\"tracks\":[]}}" 2>&1)
    
    local end_time=$(date +%s)
    local duration=$((end_time - start_time))
    total_time=$((total_time + duration))
    
    echo -e "${BLUE}⏱️  Execution time: ${duration}s${NC}"
    
    # Check for timeout
    if [ $? -eq 124 ]; then
        echo -e "${RED}⏰ TIMEOUT - Exceeded ${timeout_sec}s${NC}"
        ((fail_count++))
        return 1
    fi
    
    # Check for errors
    if echo "$output" | grep -q '"type": "error"'; then
        echo -e "${RED}❌ FAILED - Error in response${NC}"
        echo "$output" | grep '"type": "error"' | head -3
        ((fail_count++))
        return 1
    fi
    
    if echo "$output" | grep -q '"type": "tool_error"'; then
        echo -e "${RED}❌ FAILED - Tool execution error${NC}"
        echo "$output" | grep '"type": "tool_error"' | head -5
        ((fail_count++))
        return 1
    fi
    
    # Check for success
    if echo "$output" | grep -q '"type": "complete".*"success": true'; then
        echo -e "${GREEN}✅ SUCCESS${NC}"
        
        # Detailed analysis
        local track_count=$(echo "$output" | grep '"name": "stori_add_midi_track"' | wc -l)
        local region_count=$(echo "$output" | grep '"name": "stori_add_midi_region"' | wc -l)
        local note_count=$(echo "$output" | grep '"name": "stori_add_notes"' | wc -l)
        local fx_count=$(echo "$output" | grep '"name": "stori_add_insert_effect"' | wc -l)
        
        echo -e "${GREEN}📊 Statistics:${NC}"
        echo -e "  • Tracks created: $track_count"
        echo -e "  • Regions created: $region_count"
        echo -e "  • Note groups added: $note_count"
        echo -e "  • Effects added: $fx_count"
        
        # Show unique tool types
        echo -e "\n${GREEN}🛠️  Tools used:${NC}"
        echo "$output" | grep '"name":' | grep -o '"name": "[^"]*"' | sort | uniq -c | head -15
        
        ((success_count++))
        return 0
    else
        echo -e "${RED}❌ FAILED - No success confirmation${NC}"
        echo "$output" | tail -10
        ((fail_count++))
        return 1
    fi
}

echo -e "${MAGENTA}╔═══════════════════════════════════════════════════╗${NC}"
echo -e "${MAGENTA}║     STORI MAESTRO - STRESS TEST SUITE           ║${NC}"
echo -e "${MAGENTA}║     Testing System Limits & Complex Scenarios    ║${NC}"
echo -e "${MAGENTA}╚═══════════════════════════════════════════════════╝${NC}"

# CATEGORY 1: SCALE TESTS - Push track/instrument limits
echo -e "\n${YELLOW}═══ CATEGORY 1: SCALE TESTS ═══${NC}"

test_complex \
    "1.1 Orchestra - 10 Instruments" \
    "create a cinematic orchestral piece at 80 bpm with strings violins cellos brass trumpets trombones woodwinds flutes clarinets and timpani for 8 bars" \
    90

test_complex \
    "1.2 Big Band Jazz (natural commas)" \
    "make an 8 bar big band jazz arrangement at 120 bpm with trumpet, saxophone, trombone, piano, bass, drums, guitar, vibraphone, and clarinet" \
    90

test_complex \
    "1.3 Electronic Dance - Layered" \
    "create a 16 bar progressive house track at 128 bpm with kick snare hi-hats bass lead pad strings vocal chops and fx" \
    90

# CATEGORY 2: HIP HOP PRODUCTION
echo -e "\n${YELLOW}═══ CATEGORY 2: HIP HOP PRODUCTION ═══${NC}"

test_complex \
    "2.1 Boom Bap Classic (comma-separated)" \
    "make a boom bap hip hop beat at 90 bpm for 16 bars with punchy drums, jazzy bassline, rhodes piano sample, strings, and vinyl crackle" \
    60

test_complex \
    "2.2 Trap Banger" \
    "create a hard trap beat at 140 bpm for 16 bars with 808 bass rolling hi-hats snare clap synth lead dark pad and effects" \
    60

test_complex \
    "2.3 Lo-Fi Hip Hop" \
    "make a chill lofi hip hop beat at 85 bpm for 16 bars with soft drums dusty bass electric piano warm pad vinyl noise and rain sounds" \
    60

# CATEGORY 3: MIXING & EFFECTS INTENSIVE
echo -e "\n${YELLOW}═══ CATEGORY 3: MIXING & EFFECTS ═══${NC}"

test_complex \
    "3.1 Full Mix Treatment" \
    "make a 4 bar beat at 90 bpm with drums bass and melody then add compression to drums eq to bass reverb to melody and sidechain everything" \
    60

test_complex \
    "3.2 Atmospheric Soundscape" \
    "create an 8 bar ambient piece at 70 bpm with pad strings piano bass add heavy reverb delay chorus and eq to all tracks" \
    60

# CATEGORY 4: COMPLEX ARRANGEMENTS
echo -e "\n${YELLOW}═══ CATEGORY 4: COMPLEX ARRANGEMENTS ═══${NC}"

test_complex \
    "4.1 Song Structure" \
    "create a complete song at 120 bpm with intro 4 bars verse 8 bars chorus 8 bars bridge 4 bars using drums bass guitar piano and strings" \
    90

test_complex \
    "4.2 Dynamic Build" \
    "make a 16 bar progressive build at 128 bpm starting minimal with just kick then gradually adding bass hi-hats synths pads strings and effects building to a massive drop" \
    90

# CATEGORY 5: GENRE MASHUPS
echo -e "\n${YELLOW}═══ CATEGORY 5: GENRE MASHUPS ═══${NC}"

test_complex \
    "5.1 Jazz Trap Fusion" \
    "create an 8 bar jazz trap fusion at 100 bpm with 808 bass trap drums jazz piano walking bass live saxophone and modern synths" \
    60

test_complex \
    "5.2 Classical EDM" \
    "make an 8 bar classical electronic fusion at 130 bpm with orchestra strings brass woodwinds plus edm drums bass synths and drops" \
    90

# CATEGORY 6: EXTREME TESTS
echo -e "\n${YELLOW}═══ CATEGORY 6: EXTREME TESTS ═══${NC}"

test_complex \
    "6.1 Maximum Complexity" \
    "create an epic 16 bar cinematic hip hop orchestral fusion at 85 bpm with boom bap drums 808 bass strings brass woodwinds choir piano rhodes organ synth pad lead vocals and full mixing with eq compression reverb delay and sidechain on everything" \
    120

test_complex \
    "6.2 Long Form Composition" \
    "make a 32 bar progressive track at 120 bpm with drums bass piano strings starting minimal and building up adding new instruments every 4 bars with full production mixing eq reverb and effects throughout" \
    120

# SUMMARY
echo -e "\n${MAGENTA}╔═══════════════════════════════════════════════════╗${NC}"
echo -e "${MAGENTA}║              STRESS TEST SUMMARY                  ║${NC}"
echo -e "${MAGENTA}╚═══════════════════════════════════════════════════╝${NC}"
echo -e "${GREEN}✅ Passed: $success_count${NC}"
echo -e "${RED}❌ Failed: $fail_count${NC}"
echo -e "${BLUE}⏱️  Total time: ${total_time}s${NC}"
echo -e "${BLUE}📊 Average time: $((total_time / (success_count + fail_count)))s per test${NC}"

if [ $fail_count -eq 0 ]; then
    echo -e "\n${GREEN}🎉 ALL STRESS TESTS PASSED!${NC}"
    echo -e "${GREEN}🚀 System is rock solid!${NC}"
    exit 0
else
    success_rate=$((100 * success_count / (success_count + fail_count)))
    echo -e "\n${YELLOW}📊 Success rate: ${success_rate}%${NC}"
    
    if [ $success_rate -ge 80 ]; then
        echo -e "${GREEN}✅ System is performing well under stress${NC}"
    elif [ $success_rate -ge 60 ]; then
        echo -e "${YELLOW}⚠️  System has some issues under heavy load${NC}"
    else
        echo -e "${RED}❌ System needs optimization for complex scenarios${NC}"
    fi
    exit 1
fi

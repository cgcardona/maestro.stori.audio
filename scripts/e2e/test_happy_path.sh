#!/bin/bash
# Happy Path Test Suite for Stori Maestro
# Tests incrementally complex scenarios to find the working baseline

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

TOKEN="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0eXBlIjoiYWNjZXNzIiwiaWF0IjoxNzY5OTkyMTUyLCJleHAiOjE3Njk5OTkzNTIsInN1YiI6ImIzNDc1MWI4LTZhMDktNDQ5Ni05OGE1LTZmOTc1NjQ2ZTRhYSJ9.TULKva1Wem_NzZnEp7ZkoK7ItFnHUTf-_GM22tBQSkc"
MAESTRO_IP="172.18.0.8"
BASE_URL="http://$MAESTRO_IP:10001/api/v1"

success_count=0
fail_count=0

test_prompt() {
    local test_name="$1"
    local prompt="$2"
    local project_id="test-$(date +%s)-$RANDOM"
    
    echo -e "\n${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${YELLOW}TEST: $test_name${NC}"
    echo -e "${YELLOW}Prompt: \"$prompt\"${NC}"
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    
    local output=$(curl -s -N -X POST "$BASE_URL/maestro/stream" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer $TOKEN" \
        -d "{\"prompt\":\"$prompt\",\"project\":{\"projectId\":\"$project_id\",\"tracks\":[]}}" 2>&1)
    
    # Check for errors
    if echo "$output" | grep -q '"type": "error"'; then
        echo -e "${RED}❌ FAILED - Error in response${NC}"
        echo "$output" | grep '"type": "error"' | head -3
        ((fail_count++))
        return 1
    fi
    
    if echo "$output" | grep -q '"type": "tool_error"'; then
        echo -e "${RED}❌ FAILED - Tool execution error${NC}"
        echo "$output" | grep '"type": "tool_error"' | head -3
        ((fail_count++))
        return 1
    fi
    
    # Check for success
    if echo "$output" | grep -q '"type": "complete".*"success": true'; then
        echo -e "${GREEN}✅ SUCCESS${NC}"
        
        # Show what was created
        echo -e "\n${GREEN}Tool calls executed:${NC}"
        echo "$output" | grep '"type": "tool_call"' | grep -o '"name": "[^"]*"' | sort | uniq -c
        
        ((success_count++))
        return 0
    else
        echo -e "${RED}❌ FAILED - No success confirmation${NC}"
        echo "$output" | tail -10
        ((fail_count++))
        return 1
    fi
}

echo -e "${YELLOW}╔═══════════════════════════════════════════╗${NC}"
echo -e "${YELLOW}║   STORI MAESTRO - HAPPY PATH TEST       ║${NC}"
echo -e "${YELLOW}╔═══════════════════════════════════════════╗${NC}"

# Test 1: Simplest possible - single track
test_prompt "1. Single Empty Track" "create a piano track"

# Test 2: Single track with instrument specification
test_prompt "2. Track with Instrument" "create an acoustic guitar track"

# Test 3: Single drum track
test_prompt "3. Drum Track" "create a drum track"

# Test 4: Simple 2-bar drum pattern (no full composition)
test_prompt "4. Simple Drums (2 bars)" "make 2 bars of boom bap drums at 90 bpm"

# Test 5: Simple 4-bar pattern
test_prompt "5. Simple Beat (4 bars)" "make a 4 bar boom bap beat at 90 bpm"

# Test 6: Full composition with multiple instruments
test_prompt "6. Full Composition" "make a boom bap beat at 90 bpm for 4 bars with drums bass and melody"

echo -e "\n${YELLOW}╔═══════════════════════════════════════════╗${NC}"
echo -e "${YELLOW}║            TEST SUMMARY                   ║${NC}"
echo -e "${YELLOW}╚═══════════════════════════════════════════╝${NC}"
echo -e "${GREEN}✅ Passed: $success_count${NC}"
echo -e "${RED}❌ Failed: $fail_count${NC}"

if [ $fail_count -eq 0 ]; then
    echo -e "\n${GREEN}🎉 ALL TESTS PASSED!${NC}"
    exit 0
else
    echo -e "\n${RED}❌ Some tests failed${NC}"
    exit 1
fi

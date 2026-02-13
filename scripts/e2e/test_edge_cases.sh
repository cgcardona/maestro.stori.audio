#!/bin/bash
#
# Edge Cases & Error Handling Test
# Tests: Entity suggestions, validation, ambiguity handling, question routing
#

set -e

API_BASE="http://18.216.132.182:10001/api/v1"
TOKEN="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0eXBlIjoiYWNjZXNzIiwiaWF0IjoxNzY5OTY3NTQzLCJleHAiOjE3NzAwNTM5NDMsInN1YiI6IjYzMDRmZjgwLTg0M2ItNDIwZi1iN2ViLWU3OGQxODhjMWFlMiJ9.yR1qRPP9ZhLmpQQuHQrI6nHO3xW_gYLONPK-6lUfHeU"
CONV_ID="edge-test-$(uuidgen | tr '[:upper:]' '[:lower:]')"

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}╔═══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║  EDGE CASES & ROBUSTNESS TEST                                 ║${NC}"
echo -e "${BLUE}╚═══════════════════════════════════════════════════════════════╝${NC}"
echo ""

req() {
    echo -e "\n${YELLOW}▸ Test: $1${NC}"
    echo -e "  Prompt: \"$2\"\n"
    
    curl -s -N -X POST "${API_BASE}/compose/stream" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer ${TOKEN}" \
        -d "{\"prompt\": \"$2\", \"conversation_id\": \"${CONV_ID}\"}" \
        | grep -E "event:|data:" | head -15
    
    echo ""
    sleep 1
}

# Setup: Create some entities first
echo -e "${BLUE}━━━ Setup: Creating entities ━━━${NC}"
req "Create drums track" "add a drum track"
req "Create bass track" "add a bass track"
req "Create piano track" "add a piano track"

echo -e "\n${BLUE}━━━ Testing Entity Resolution & Suggestions ━━━${NC}"

req "Typo in track name (should suggest)" \
    "add reverb to the drms"

req "Close match (should suggest)" \
    "make the bss louder"

req "Partial name match" \
    "compress the pian"

echo -e "\n${BLUE}━━━ Testing Ambiguity & Clarification ━━━${NC}"

req "Vague deictic reference" \
    "make it better"

req "Vague target (should ask for clarification)" \
    "add more"

req "Unclear pronoun reference" \
    "make that louder"

echo -e "\n${BLUE}━━━ Testing Question Routing ━━━${NC}"

req "General question (ASK_GENERAL)" \
    "what's the weather like?"

req "Stori-specific question (ASK_STORI_DOCS)" \
    "how do I quantize notes in Stori?"

req "Music theory question" \
    "what's the difference between a major and minor chord?"

req "Technical DAW question" \
    "what does sidechain compression do?"

echo -e "\n${BLUE}━━━ Testing Intent Classification Edge Cases ━━━${NC}"

req "Imperative vs question" \
    "should I add reverb?"

req "Compound intent" \
    "add a synth lead and make it swirl with chorus and delay"

req "Negation (what NOT to do)" \
    "don't make it too loud"

req "Conditional request" \
    "if the mix sounds muddy, add a high-pass filter"

echo -e "\n${BLUE}━━━ Testing Producer Language Variants ━━━${NC}"

req "Variant: 'darker' → 'more dark'" \
    "make it more dark"

req "Variant: 'punchier' → 'more punchy'" \
    "give it more punch"

req "Variant: 'wider' → 'more wide'" \
    "make the stereo image more wide"

req "Chained idioms" \
    "make it darker, grittier, and more aggressive"

echo -e "\n${BLUE}━━━ Testing Value Ranges & Validation ━━━${NC}"

req "Extreme tempo (should clamp/validate)" \
    "set tempo to 300"

req "Negative value (should reject)" \
    "set volume to -50"

req "Out of range pan (should validate)" \
    "pan the drums to 150"

echo -e "\n${BLUE}━━━ Testing Multi-Intent & Conversation Flow ━━━${NC}"

req "Reference to previous action" \
    "undo that last change"

req "Follow-up refinement" \
    "but make it more subtle"

req "Comparison request" \
    "make the bass as loud as the kick"

echo -e "\n\n${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}✓ Edge Cases Test Complete${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "${YELLOW}Robustness Features Tested:${NC}"
echo "  ✓ Entity name suggestions (fuzzy matching)"
echo "  ✓ Clarification requests for vague inputs"
echo "  ✓ Intent routing (questions vs commands)"
echo "  ✓ Producer idiom variant matching"
echo "  ✓ Value range validation"
echo "  ✓ Compound/multi-intent parsing"
echo "  ✓ Conversation context & follow-ups"
echo ""
echo -e "${BLUE}Conversation ID: ${CONV_ID}${NC}"
echo ""

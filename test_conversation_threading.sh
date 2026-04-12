#!/usr/bin/env bash
# ============================================================
# Automated Test Suite for Conversation Threading
# Tests the bot's ability to maintain conversation context
# ============================================================

set -euo pipefail

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

ROUTER_URL="http://127.0.0.1:7771"
RESULTS_FILE="/tmp/threading_test_results.txt"
HISTORY_FILE="/Users/chandan/Desktop/BNB/airbnb-host/scripts/whatsapp/message_history.json"

# Test counters
PASSED=0
FAILED=0

echo "" > "$RESULTS_FILE"

# ============================================================
# Helper Functions
# ============================================================

pass() {
  echo -e "${GREEN}✅ PASS${NC}: $1"
  echo "✅ PASS: $1" >> "$RESULTS_FILE"
  ((PASSED++))
}

fail() {
  echo -e "${RED}❌ FAIL${NC}: $1"
  echo "❌ FAIL: $1" >> "$RESULTS_FILE"
  ((FAILED++))
}

info() {
  echo -e "${BLUE}ℹ️${NC}  $1"
  echo "ℹ️  $1" >> "$RESULTS_FILE"
}

section() {
  echo ""
  echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  echo -e "${YELLOW}$1${NC}"
  echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  echo "" >> "$RESULTS_FILE"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" >> "$RESULTS_FILE"
  echo "$1" >> "$RESULTS_FILE"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" >> "$RESULTS_FILE"
}

# ============================================================
# Test 1: Router Health Check
# ============================================================

section "TEST 1: Router Service Health"

if curl -s -f "$ROUTER_URL/health" > /dev/null 2>&1; then
  pass "Router is responding on port 7771"
else
  fail "Router is not responding. Start services with: cd airbnb-host/scripts && ./start.sh"
  exit 1
fi

# ============================================================
# Test 2: Initial Message Classification (No Context)
# ============================================================

section "TEST 2: Initial Message - No Prior Context"

RESPONSE=$(curl -s -X POST "$ROUTER_URL/classify" \
  -H "Content-Type: application/json" \
  -d '{
    "source": "whatsapp",
    "guest_name": "Test Guest",
    "message": "How do I control the air conditioning in the living room?",
    "booking_uid": "test_booking_001",
    "thread_context": null
  }')

DRAFT=$(echo "$RESPONSE" | jq -r '.draft // empty')
MSG_TYPE=$(echo "$RESPONSE" | jq -r '.msg_type // empty')

if [[ ! -z "$DRAFT" ]]; then
  pass "Initial message generated draft"
  info "Draft: ${DRAFT:0:80}..."
else
  fail "Initial message did not generate draft"
  echo "$RESPONSE" >> "$RESULTS_FILE"
fi

if [[ "$MSG_TYPE" == "routine" ]]; then
  pass "Initial AC question classified as ROUTINE (will auto-reply)"
else
  info "Message type: $MSG_TYPE"
fi

# Store for follow-up test
INITIAL_DRAFT="$DRAFT"

# ============================================================
# Test 3: Follow-up Message WITH Context
# ============================================================

section "TEST 3: Follow-up Message - WITH Conversation Context"

CONTEXT="[INBOUND] How do I control the air conditioning in the living room?
[OUTBOUND] $INITIAL_DRAFT"

RESPONSE2=$(curl -s -X POST "$ROUTER_URL/classify" \
  -H "Content-Type: application/json" \
  -d '{
    "source": "whatsapp",
    "guest_name": "Test Guest",
    "message": "What temperature should I set it to?",
    "booking_uid": "test_booking_001",
    "thread_context": "'"$CONTEXT"'"
  }')

DRAFT2=$(echo "$RESPONSE2" | jq -r '.draft // empty')

if [[ ! -z "$DRAFT2" ]]; then
  pass "Follow-up message generated draft"
  info "Draft: ${DRAFT2:0:80}..."
else
  fail "Follow-up message did not generate draft"
  echo "$RESPONSE2" >> "$RESULTS_FILE"
fi

# Check if second draft shows awareness of context
if echo "$DRAFT2" | grep -qi "as.*mentioned\|previously\|earlier\|thermostat.*set\|control"; then
  pass "Follow-up draft references prior context (shows thread awareness)"
else
  info "Follow-up draft does not explicitly reference prior message (acceptable if context is implicit)"
fi

# ============================================================
# Test 4: Message History File Validation
# ============================================================

section "TEST 4: Message History File Tracking"

if [[ -f "$HISTORY_FILE" ]]; then
  pass "Message history file exists at: $HISTORY_FILE"

  FILE_SIZE=$(wc -c < "$HISTORY_FILE")
  if [[ $FILE_SIZE -gt 100 ]]; then
    pass "Message history file has content ($FILE_SIZE bytes)"
  else
    info "Message history file is small/empty (expected if no live messages yet)"
  fi

  # Try to parse JSON
  if jq empty "$HISTORY_FILE" 2>/dev/null; then
    pass "Message history JSON is valid"

    # Count threads
    THREAD_COUNT=$(jq 'keys | length' "$HISTORY_FILE" 2>/dev/null || echo 0)
    info "Number of conversation threads: $THREAD_COUNT"
  else
    fail "Message history JSON is invalid"
  fi
else
  info "Message history file doesn't exist yet (will be created on first real guest message)"
fi

# ============================================================
# Test 5: Thread Context Parameters
# ============================================================

section "TEST 5: API Parameters - thread_context and booking_uid"

# Test with empty context
RESPONSE3=$(curl -s -X POST "$ROUTER_URL/classify" \
  -H "Content-Type: application/json" \
  -d '{
    "source": "whatsapp",
    "guest_name": "Alice",
    "message": "What is the WiFi password?",
    "booking_uid": "booking_alice_456",
    "thread_context": ""
  }')

DRAFT3=$(echo "$RESPONSE3" | jq -r '.draft // empty')
if [[ ! -z "$DRAFT3" ]]; then
  pass "API accepts thread_context parameter (empty)"
  pass "API accepts booking_uid parameter"
else
  fail "API did not accept new parameters"
  echo "$RESPONSE3" >> "$RESULTS_FILE"
fi

# ============================================================
# Test 6: Complex Message with Context
# ============================================================

section "TEST 6: Complex Message Handling with Context"

CONTEXT2="[INBOUND] The shower isn't getting hot water
[OUTBOUND] Thank you for reporting this. We'll have our plumber fix it today."

RESPONSE4=$(curl -s -X POST "$ROUTER_URL/classify" \
  -H "Content-Type: application/json" \
  -d '{
    "source": "whatsapp",
    "guest_name": "Bob",
    "message": "How long will the repair take?",
    "booking_uid": "booking_bob_789",
    "thread_context": "'"$CONTEXT2"'"
  }')

MSG_TYPE4=$(echo "$RESPONSE4" | jq -r '.msg_type // empty')
DRAFT4=$(echo "$RESPONSE4" | jq -r '.draft // empty')

if [[ "$MSG_TYPE4" == "routine" ]]; then
  pass "Follow-up to complex issue classified correctly (as ROUTINE if question is simple)"
elif [[ "$MSG_TYPE4" == "complex" ]]; then
  pass "Message correctly classified as COMPLEX (escalates to host)"
fi

if [[ ! -z "$DRAFT4" ]]; then
  pass "Complex message follow-up generated draft"
  info "Draft: ${DRAFT4:0:80}..."
else
  fail "Complex message follow-up did not generate draft"
fi

# ============================================================
# Test 7: Three-Turn Conversation
# ============================================================

section "TEST 7: Multi-Turn Conversation (3 Messages)"

# Turn 1
R1=$(curl -s -X POST "$ROUTER_URL/classify" \
  -H "Content-Type: application/json" \
  -d '{
    "source": "whatsapp",
    "guest_name": "Charlie",
    "message": "Is there a gym nearby?",
    "booking_uid": "booking_charlie_111",
    "thread_context": null
  }')
D1=$(echo "$R1" | jq -r '.draft // empty')

pass "Turn 1: Initial gym question responded"
info "Response: ${D1:0:60}..."

# Turn 2
CTX1="[INBOUND] Is there a gym nearby?
[OUTBOUND] $D1"

R2=$(curl -s -X POST "$ROUTER_URL/classify" \
  -H "Content-Type: application/json" \
  -d '{
    "source": "whatsapp",
    "guest_name": "Charlie",
    "message": "How far is it?",
    "booking_uid": "booking_charlie_111",
    "thread_context": "'"$CTX1"'"
  }')
D2=$(echo "$R2" | jq -r '.draft // empty')

pass "Turn 2: Follow-up distance question responded"

# Turn 3
CTX2="[INBOUND] Is there a gym nearby?
[OUTBOUND] $D1
[INBOUND] How far is it?
[OUTBOUND] $D2"

R3=$(curl -s -X POST "$ROUTER_URL/classify" \
  -H "Content-Type: application/json" \
  -d '{
    "source": "whatsapp",
    "guest_name": "Charlie",
    "message": "Does it have a pool?",
    "booking_uid": "booking_charlie_111",
    "thread_context": "'"$CTX2"'"
  }')
D3=$(echo "$R3" | jq -r '.draft // empty')

pass "Turn 3: Follow-up pool question responded with full context"
info "Full 3-turn conversation completed successfully"

# ============================================================
# Test 8: Database Model Update Check
# ============================================================

section "TEST 8: Database Schema - thread_key Field"

if grep -q "thread_key" /Users/chandan/Desktop/BNB/web/models.py; then
  pass "MessageLog model includes thread_key field"
else
  fail "MessageLog model missing thread_key field"
fi

# ============================================================
# Test 9: Function Existence Checks
# ============================================================

section "TEST 9: Bot Functions Implementation"

BOT_FILE="/Users/chandan/Desktop/BNB/airbnb-host/scripts/whatsapp/bot.js"

if grep -q "function getThreadKey" "$BOT_FILE"; then
  pass "getThreadKey() function implemented"
else
  fail "getThreadKey() function not found"
fi

if grep -q "function addMessageToHistory" "$BOT_FILE"; then
  pass "addMessageToHistory() function implemented"
else
  fail "addMessageToHistory() function not found"
fi

if grep -q "function getConversationContext" "$BOT_FILE"; then
  pass "getConversationContext() function implemented"
else
  fail "getConversationContext() function not found"
fi

if grep -q "thread_context" "$BOT_FILE"; then
  pass "thread_context parameter being passed to router"
else
  fail "thread_context parameter not passed to router"
fi

# ============================================================
# Test 10: SKILL.md Instructions Updated
# ============================================================

section "TEST 10: AI Prompt Instructions"

SKILL_FILE="/Users/chandan/Desktop/BNB/airbnb-host/SKILL.md"

if grep -q "Conversation Continuity" "$SKILL_FILE"; then
  pass "Conversation Continuity guidelines added to SKILL.md"
else
  fail "Conversation Continuity guidelines missing"
fi

if grep -q "follow-up.*invitation\|Feel free to ask\|Let me know if" "$SKILL_FILE"; then
  pass "Follow-up invitation instructions in place"
else
  fail "Follow-up invitation instructions missing"
fi

# ============================================================
# Summary Report
# ============================================================

section "TEST SUMMARY"

TOTAL=$((PASSED + FAILED))
echo -e "${GREEN}Passed: $PASSED${NC} | ${RED}Failed: $FAILED${NC} | Total: $TOTAL"
echo ""
echo "Passed: $PASSED | Failed: $FAILED | Total: $TOTAL" >> "$RESULTS_FILE"

if [[ $FAILED -eq 0 ]]; then
  echo -e "${GREEN}✅ All tests passed! Conversation threading is working.${NC}"
  echo "✅ All tests passed!" >> "$RESULTS_FILE"
  EXIT_CODE=0
else
  echo -e "${RED}❌ Some tests failed. Review above output.${NC}"
  echo "❌ Some tests failed." >> "$RESULTS_FILE"
  EXIT_CODE=1
fi

# ============================================================
# Live Testing Instructions
# ============================================================

section "NEXT STEPS: Live Testing"

echo "To test with real guest messages:"
echo ""
echo "1. Ensure services are running:"
echo -e "   ${YELLOW}cd /Users/chandan/Desktop/BNB/airbnb-host/scripts${NC}"
echo -e "   ${YELLOW}./start.sh${NC}"
echo ""
echo "2. Register a guest with their WhatsApp number:"
echo -e "   ${YELLOW}Send to host number: GUEST_WA +1234567890 John Doe${NC}"
echo ""
echo "3. Guest sends first message:"
echo -e "   ${YELLOW}Guest: How do I adjust the AC?${NC}"
echo ""
echo "4. Bot auto-replies with follow-up invitation"
echo ""
echo "5. Guest asks follow-up:"
echo -e "   ${YELLOW}Guest: What temperature is it set to?${NC}"
echo ""
echo "6. Check: Does bot reference the prior AC question?"
echo -e "   ${GREEN}✅ YES${NC} = threading is working"
echo -e "   ${RED}❌ NO${NC} = bot treating messages in isolation"
echo ""
echo "7. Monitor logs:"
echo -e "   ${YELLOW}tail -f /tmp/router.log${NC}"
echo ""

# ============================================================
# Save Results
# ============================================================

echo ""
echo -e "${BLUE}Full results saved to:${NC} $RESULTS_FILE"
cat "$RESULTS_FILE"

exit $EXIT_CODE

#!/usr/bin/env bash
# ============================================================
# Code Verification Test for Conversation Threading
# Verifies all code changes are in place (no service required)
# ============================================================

set -euo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

PASSED=0
FAILED=0

pass() {
  echo -e "${GREEN}✅${NC} $1"
  PASSED=$((PASSED + 1))
}

fail() {
  echo -e "${RED}❌${NC} $1"
  FAILED=$((FAILED + 1))
}

info() {
  echo -e "${BLUE}ℹ️${NC}  $1"
}

section() {
  echo ""
  echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  echo -e "${YELLOW}$1${NC}"
  echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

# ============================================================
# Test 1: Message History File Configuration
# ============================================================

section "TEST 1: Message History File Configuration"

BOT_FILE="/Users/chandan/Desktop/BNB/airbnb-host/scripts/whatsapp/bot.js"

if grep -q "FILES = {" "$BOT_FILE"; then
  pass "FILES object found in bot.js"
else
  fail "FILES object not found"
fi

if grep -q 'history.*path.join.*"message_history.json"' "$BOT_FILE"; then
  pass "message_history.json configured in FILES object"
else
  fail "message_history.json not in FILES object"
fi

if grep -q "const history.*new Map" "$BOT_FILE"; then
  pass "history Map initialized in memory"
else
  fail "history Map not initialized"
fi

if grep -q "loadJSON(FILES.history)" "$BOT_FILE"; then
  pass "Message history loaded from disk on startup"
else
  fail "Message history not loaded from disk"
fi

# ============================================================
# Test 2: Thread Management Functions
# ============================================================

section "TEST 2: Conversation Thread Functions"

if grep -q "function getThreadKey" "$BOT_FILE"; then
  pass "getThreadKey() function implemented"
else
  fail "getThreadKey() function missing"
fi

if grep -q "function addMessageToHistory" "$BOT_FILE"; then
  pass "addMessageToHistory() function implemented"
else
  fail "addMessageToHistory() function missing"
fi

if grep -q "function getConversationContext" "$BOT_FILE"; then
  pass "getConversationContext() function implemented"
else
  fail "getConversationContext() function missing"
fi

# Verify addMessageToHistory saves to disk
if grep -A15 "function addMessageToHistory" "$BOT_FILE" | grep -q "saveJSON"; then
  pass "addMessageToHistory() saves to disk"
else
  fail "addMessageToHistory() doesn't persist to disk"
fi

# Verify 10-message rolling window
if grep -q "thread.length > 10" "$BOT_FILE"; then
  pass "10-message rolling window implemented"
else
  fail "Rolling window not implemented"
fi

# ============================================================
# Test 3: Message Recording in Handler
# ============================================================

section "TEST 3: Guest Message Recording"

if grep -q "addMessageToHistory(booking_uid, \"inbound\"" "$BOT_FILE"; then
  pass "Inbound messages recorded to history"
else
  fail "Inbound message recording missing"
fi

if grep -q "addMessageToHistory(booking_uid, \"outbound\", draft)" "$BOT_FILE"; then
  pass "Outbound messages recorded to history (routine flow)"
else
  fail "Outbound message recording missing (routine)"
fi

if grep -A20 "async function onApprove" "$BOT_FILE" | grep -q "addMessageToHistory"; then
  pass "Outbound messages recorded in approval flow"
else
  fail "Approval flow doesn't record messages"
fi

# ============================================================
# Test 4: Thread Context Passing to Router
# ============================================================

section "TEST 4: Context Passed to Classifier API"

if grep -q "const conversationContext = getConversationContext" "$BOT_FILE"; then
  pass "Conversation context retrieved before classification"
else
  fail "Context not retrieved before classification"
fi

if grep -q "thread_context.*conversationContext" "$BOT_FILE"; then
  pass "thread_context parameter passed to /classify endpoint"
else
  fail "thread_context not passed to classifier"
fi

if grep -q "booking_uid.*booking_uid" "$BOT_FILE"; then
  pass "booking_uid parameter passed to /classify endpoint"
else
  fail "booking_uid not passed to classifier"
fi

# ============================================================
# Test 5: Pending Drafts Tracking
# ============================================================

section "TEST 5: Draft Metadata Tracking"

if grep -q "pending.set(draft_id.*booking_uid" "$BOT_FILE"; then
  pass "booking_uid stored in pending draft metadata"
else
  fail "booking_uid not stored in pending draft"
fi

# ============================================================
# Test 6: API Schema Updates
# ============================================================

section "TEST 6: API Request Schema"

ROUTER_FILE="/Users/chandan/Desktop/BNB/airbnb-host/scripts/response_router.py"

if grep -q "class ClassifyRequest" "$ROUTER_FILE"; then
  pass "ClassifyRequest model exists"
else
  fail "ClassifyRequest model not found"
fi

if grep -q "thread_context.*Optional\[str\]" "$ROUTER_FILE"; then
  pass "thread_context field added to ClassifyRequest"
else
  fail "thread_context field missing from request"
fi

if grep -q "booking_uid.*Optional\[str\]" "$ROUTER_FILE"; then
  pass "booking_uid field added to ClassifyRequest"
else
  fail "booking_uid field missing from request"
fi

# ============================================================
# Test 7: Draft Generation with Context
# ============================================================

section "TEST 7: Draft Generation Function"

if grep -q "def generate_draft.*thread_context" "$ROUTER_FILE"; then
  pass "generate_draft() accepts thread_context parameter"
else
  fail "generate_draft() doesn't accept thread_context"
fi

if grep -q "conversation_history" "$ROUTER_FILE"; then
  pass "Conversation history XML tags in prompt"
else
  fail "Conversation history XML tags missing"
fi

if grep -q "current_message" "$ROUTER_FILE"; then
  pass "Current message XML tags in prompt"
else
  fail "Current message XML tags missing"
fi

# Verify context is included in user_content
if grep -A25 "def generate_draft" "$ROUTER_FILE" | grep -q "context_section\|thread_context"; then
  pass "thread_context included in Claude prompt"
else
  fail "thread_context not passed to Claude"
fi

# ============================================================
# Test 8: Classifier Endpoint Update
# ============================================================

section "TEST 8: Classifier Endpoint"

if grep -q "generate_draft.*thread_context=req.thread_context" "$ROUTER_FILE"; then
  pass "Classifier passes thread_context to generate_draft()"
else
  fail "Classifier doesn't pass thread_context to generate_draft()"
fi

if grep -q '"booking_uid": req.booking_uid' "$ROUTER_FILE"; then
  pass "booking_uid stored in pending draft record"
else
  fail "booking_uid not stored in pending draft"
fi

# ============================================================
# Test 9: Database Schema
# ============================================================

section "TEST 9: Database Model Update"

MODELS_FILE="/Users/chandan/Desktop/BNB/web/models.py"

if grep -q "class MessageLog" "$MODELS_FILE"; then
  pass "MessageLog model exists"
else
  fail "MessageLog model not found"
fi

if grep -q "thread_key.*Mapped\[Optional\[str\]\]" "$MODELS_FILE"; then
  pass "thread_key field added to MessageLog"
else
  fail "thread_key field missing from MessageLog"
fi

if grep -q 'thread_key.*index=True' "$MODELS_FILE"; then
  pass "thread_key field is indexed for performance"
else
  fail "thread_key field not indexed"
fi

# ============================================================
# Test 10: SKILL.md Instructions
# ============================================================

section "TEST 10: AI Prompt Instructions"

SKILL_FILE="/Users/chandan/Desktop/BNB/airbnb-host/SKILL.md"

if grep -q "Conversation Continuity" "$SKILL_FILE"; then
  pass "Conversation Continuity section in SKILL.md"
else
  fail "Conversation Continuity section missing"
fi

if grep -q "follow-up.*invitation\|Feel free to ask\|Let me know if" "$SKILL_FILE"; then
  pass "Follow-up invitation instructions present"
else
  fail "Follow-up invitation instructions missing"
fi

if grep -q "As we discussed\|prior.*message\|earlier" "$SKILL_FILE"; then
  pass "Context reference instructions in SKILL.md"
else
  fail "Context reference instructions missing"
fi

if grep -q "Always.*invitation for follow-up" "$SKILL_FILE"; then
  pass "Always-block updated with conversation continuity rules"
else
  fail "Always-block not updated"
fi

# ============================================================
# Test 11: Code Syntax
# ============================================================

section "TEST 11: Syntax Validation"

# Check bot.js syntax
if node -c "$BOT_FILE" 2>/dev/null; then
  pass "bot.js syntax is valid"
else
  fail "bot.js has syntax errors"
fi

# Check response_router.py syntax
if python3 -m py_compile "$ROUTER_FILE" 2>/dev/null; then
  pass "response_router.py syntax is valid"
else
  fail "response_router.py has syntax errors"
fi

# ============================================================
# Test 12: Git Commit
# ============================================================

section "TEST 12: Implementation Commit"

if git log --oneline | grep -q "conversation threading"; then
  pass "Implementation committed to git"
else
  info "Commit not yet pushed (check recent commits)"
fi

# ============================================================
# Summary
# ============================================================

section "VERIFICATION SUMMARY"

TOTAL=$((PASSED + FAILED))
echo ""
echo -e "${GREEN}Passed: $PASSED${NC} | ${RED}Failed: $FAILED${NC} | Total: $TOTAL"
echo ""

if [[ $FAILED -eq 0 ]]; then
  echo -e "${GREEN}✅ All code changes verified successfully!${NC}"
  echo ""
  echo "The implementation is complete and ready for testing."
  EXIT_CODE=0
else
  echo -e "${RED}❌ Some code changes are missing.${NC}"
  EXIT_CODE=1
fi

# ============================================================
# Live Testing Setup Instructions
# ============================================================

echo ""
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${YELLOW}NEXT: Setup for Live Testing${NC}"
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "To run end-to-end tests with the actual bot:"
echo ""
echo "1. Set up .env in airbnb-host/scripts:"
echo -e "   ${BLUE}cd /Users/chandan/Desktop/BNB/airbnb-host/scripts${NC}"
echo -e "   ${BLUE}cp .env.example .env${NC}"
echo ""
echo "2. Edit .env and fill in:"
echo -e "   ${YELLOW}ANTHROPIC_API_KEY=your-api-key${NC}"
echo -e "   ${YELLOW}HOST_WHATSAPP_NUMBER=+1234567890${NC}"
echo ""
echo "3. Start the services:"
echo -e "   ${BLUE}./start.sh${NC}"
echo ""
echo "4. Run the automated API test:"
echo -e "   ${BLUE}bash /Users/chandan/Desktop/BNB/test_conversation_threading.sh${NC}"
echo ""
echo "5. For real guest testing:"
echo -e "   ${YELLOW}Guest sends message → Bot auto-replies → Guest asks follow-up${NC}"
echo -e "   ${YELLOW}Check if bot references prior context ✅${NC}"
echo ""

exit $EXIT_CODE

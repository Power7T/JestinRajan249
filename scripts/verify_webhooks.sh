#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${1:-${BASE_URL:-}}"
INBOUND_SECRET="${INBOUND_SECRET:-${INBOUND_PARSE_WEBHOOK_SECRET:-}}"
INBOUND_ALIAS="${INBOUND_ALIAS:-}"
INBOUND_DOMAIN="${INBOUND_DOMAIN:-inbound.hostai.local}"

if [[ -z "${BASE_URL}" ]]; then
  echo "Usage: BASE_URL=https://your-public-url ./scripts/verify_webhooks.sh" >&2
  echo "   or: ./scripts/verify_webhooks.sh https://your-public-url" >&2
  exit 1
fi

BASE_URL="${BASE_URL%/}"

tmp_files=()
cleanup() {
  if [[ ${#tmp_files[@]} -gt 0 ]]; then
    rm -f "${tmp_files[@]}"
  fi
}
trap cleanup EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

pass() {
  echo "OK: $*"
}

make_tmp() {
  local file
  file="$(mktemp)"
  tmp_files+=("${file}")
  printf '%s\n' "${file}"
}

http_code() {
  local code
  code="$(curl -ksS -o /dev/null -w "%{http_code}" "$@")"
  printf '%s\n' "${code}"
}

headers_file="$(make_tmp)"
login_body_file="$(make_tmp)"

ping_code="$(http_code "${BASE_URL}/ping")"
[[ "${ping_code}" == "200" ]] || fail "GET /ping returned ${ping_code}"
pass "GET /ping returned 200"

login_code="$(curl -ksS -D "${headers_file}" -o "${login_body_file}" -w "%{http_code}" "${BASE_URL}/login")"
[[ "${login_code}" == "200" ]] || fail "GET /login returned ${login_code}"
grep -qi '^set-cookie: .*csrf_token=' "${headers_file}" || fail "GET /login did not issue a CSRF cookie"
if [[ "${BASE_URL}" == https://* ]]; then
  grep -qi '^set-cookie: .*Secure' "${headers_file}" || fail "Expected a Secure cookie over HTTPS"
fi
pass "GET /login issued a CSRF cookie with expected security flags"

stripe_code="$(http_code \
  -X POST "${BASE_URL}/billing/stripe-webhook" \
  -H "content-type: application/json" \
  -H "stripe-signature: invalid" \
  --data '{}'
)"
[[ "${stripe_code}" == "400" ]] || fail "Stripe webhook returned ${stripe_code}; expected 400 for an invalid signature"
pass "Stripe webhook rejected an invalid signature with 400"

if [[ -n "${INBOUND_SECRET}" ]]; then
  recipient_alias="${INBOUND_ALIAS:-smoke-test}"
  inbound_code="$(http_code \
    -X POST "${BASE_URL}/email/inbound" \
    -H "X-Inbound-Webhook-Secret: ${INBOUND_SECRET}" \
    --data-urlencode "recipient=${recipient_alias}@${INBOUND_DOMAIN}" \
    --data-urlencode "from=Guest <guest@example.com>" \
    --data-urlencode "subject=Tunnel webhook smoke test" \
    --data-urlencode "text=Hello from the public smoke test"
  )"
  case "${inbound_code}" in
    200|404|422)
      pass "Inbound email webhook accepted authenticated traffic (HTTP ${inbound_code})"
      ;;
    403)
      fail "Inbound email webhook still returned 403 with the configured secret"
      ;;
    5*)
      fail "Inbound email webhook returned ${inbound_code}"
      ;;
    *)
      fail "Inbound email webhook returned unexpected status ${inbound_code}"
      ;;
  esac
else
  inbound_code="$(http_code \
    -X POST "${BASE_URL}/email/inbound" \
    --data-urlencode "recipient=smoke-test@${INBOUND_DOMAIN}" \
    --data-urlencode "from=Guest <guest@example.com>" \
    --data-urlencode "subject=Tunnel webhook smoke test" \
    --data-urlencode "text=Hello from the public smoke test"
  )"
  [[ "${inbound_code}" == "403" ]] || fail "Inbound email webhook returned ${inbound_code}; expected 403 without a secret"
  pass "Inbound email webhook rejected unauthenticated traffic with 403"
fi

echo
echo "Webhook smoke checks passed for ${BASE_URL}"

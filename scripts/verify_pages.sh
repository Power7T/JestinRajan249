#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${1:-${BASE_URL:-}}"
EMAIL="${EMAIL:-}"
PASSWORD="${PASSWORD:-}"

if [[ -z "${BASE_URL}" || -z "${EMAIL}" || -z "${PASSWORD}" ]]; then
  cat >&2 <<'USAGE'
Usage:
  BASE_URL=https://your-public-url EMAIL=you@example.com PASSWORD='...' ./scripts/verify_pages.sh
Or:
  EMAIL=you@example.com PASSWORD='...' ./scripts/verify_pages.sh https://your-public-url
USAGE
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

make_tmp() {
  local file
  file="$(mktemp)"
  tmp_files+=("${file}")
  printf '%s\n' "${file}"
}

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

pass() {
  echo "OK: $*"
}

cookie_jar="$(make_tmp)"
login_headers="$(make_tmp)"

curl -ksS -c "${cookie_jar}" -D "${login_headers}" -o /dev/null "${BASE_URL}/login" || fail "GET /login failed"

csrf_token="$(awk -F $'\t' '$6=="csrf_token"{print $7}' "${cookie_jar}" | tail -n 1)"
[[ -n "${csrf_token}" ]] || fail "GET /login did not set csrf_token cookie"
pass "GET /login set csrf_token cookie"

login_code="$(
  curl -ksS -o /dev/null -w "%{http_code}" \
    -b "${cookie_jar}" -c "${cookie_jar}" \
    -X POST "${BASE_URL}/login" \
    --data-urlencode "email=${EMAIL}" \
    --data-urlencode "password=${PASSWORD}" \
    --data-urlencode "csrf_token=${csrf_token}"
)"
case "${login_code}" in
  302|303) pass "POST /login returned ${login_code} (redirect)" ;;
  200)     pass "POST /login returned 200 (already logged in?)" ;;
  4*|5*)   fail "POST /login returned ${login_code}" ;;
  *)       fail "POST /login returned unexpected status ${login_code}" ;;
esac

check_page() {
  local path="$1"
  local code
  code="$(curl -ksS -o /dev/null -w "%{http_code}" -b "${cookie_jar}" "${BASE_URL}${path}")"
  case "${code}" in
    200) pass "GET ${path} returned 200" ;;
    302|303) fail "GET ${path} redirected (${code}) — auth/session likely failed" ;;
    404) fail "GET ${path} returned 404 (route missing)" ;;
    5*) fail "GET ${path} returned ${code}" ;;
    *) fail "GET ${path} returned unexpected status ${code}" ;;
  esac
}

check_page "/dashboard"
check_page "/conversations"
check_page "/reservations"
check_page "/activity"
check_page "/analytics"
check_page "/analytics/roi"
check_page "/voice-calls"
check_page "/voice-calls?tab=settings"
check_page "/workflow"
check_page "/settings"
check_page "/billing"

echo
echo "Authenticated page smoke checks passed for ${BASE_URL}"


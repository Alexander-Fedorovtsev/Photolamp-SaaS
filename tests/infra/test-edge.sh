#!/usr/bin/env bash
# Disposable CI environment only; never run this harness on the SelfOS server.
set -Eeuo pipefail
cd "$(dirname "$0")/../.."
test_dir="$(mktemp -d /tmp/photolamp-edge.XXXXXX)"
export STAGING_TEST_PROJECT="photolamp-edge-$RANDOM"
export STAGING_TEST_EDGE_NETWORK="$STAGING_TEST_PROJECT-network"
export STAGING_TEST_CERT_DIR="$test_dir/certs"
export STAGING_TEST_DATA_DIR="$test_dir/postgres"
export PHOTOLAMP_DEPLOY_SHA="${PHOTOLAMP_DEPLOY_SHA:-$(git rev-parse HEAD)}"
export POSTGRES_PASSWORD
POSTGRES_PASSWORD="$(openssl rand -hex 32)"
# Public, synthetic test credentials. Do not use these in server configuration.
# shellcheck disable=SC2016
export TRAEFIK_BASIC_AUTH_USERS='demo-test:$2b$05$68B.eqKkR1S8P23Fevk9Luq2JPiasI8FL.MGS9GC37gS5LxTcoSFC'
compose=(docker compose -p "$STAGING_TEST_PROJECT" -f compose.yaml -f compose.staging.yaml)
if [[ "${PHOTOLAMP_CLOUD_BUILD:-false}" == true ]]; then compose+=(-f infra/compose.cloud.yaml); fi
compose+=(-f tests/infra/compose.edge.yaml)
cleanup() {
  local result="$?"
  trap - EXIT
  if [[ "$result" -ne 0 ]]; then "${compose[@]}" logs --no-color >&2 || true; fi
  "${compose[@]}" down --volumes >/dev/null 2>&1 || true
  docker network rm "$STAGING_TEST_EDGE_NETWORK" >/dev/null 2>&1 || true
  # PostgreSQL owns its test directory; remove it from a temporary container.
  docker run --rm -v "$test_dir:/cleanup" postgres:16-bookworm \
    sh -c 'rm -rf /cleanup/postgres' >/dev/null 2>&1 || true
  rm -rf -- "$test_dir"
  exit "$result"
}
trap cleanup EXIT
mkdir -p "$STAGING_TEST_CERT_DIR" "$STAGING_TEST_DATA_DIR"
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=localhost \
  -addext subjectAltName=DNS:localhost \
  -keyout "$STAGING_TEST_CERT_DIR/key.pem" -out "$STAGING_TEST_CERT_DIR/cert.pem" 2>/dev/null
cat > "$STAGING_TEST_CERT_DIR/tls.yaml" <<'YAML'
tls:
  certificates:
    - certFile: /etc/traefik/test/cert.pem
      keyFile: /etc/traefik/test/key.pem
YAML
docker network create "$STAGING_TEST_EDGE_NETWORK" >/dev/null
export STAGING_TEST_NO_PROXY
STAGING_TEST_NO_PROXY="${NO_PROXY:-localhost,127.0.0.1},$(docker network inspect "$STAGING_TEST_EDGE_NETWORK" --format '{{(index .IPAM.Config 0).Subnet}}')"
"${compose[@]}" up --build --wait --wait-timeout 180
curl_options=(--silent --show-error --max-time 10 --cacert "$STAGING_TEST_CERT_DIR/cert.pem"
  -H 'Host: photolamp-test.anliwei.pro')
readonly test_url="https://localhost:${STAGING_TEST_PORT:-3443}"
auth_ready=false
for attempt in {1..20}; do
  code="$(curl "${curl_options[@]}" -o /dev/null -w '%{http_code}' "$test_url/api/health/ready" || true)"
  if [[ "$code" == 401 ]]; then auth_ready=true; break; fi
  printf 'Waiting for the test edge (%s/20)\n' "$attempt"
  sleep 1
done
[[ "$auth_ready" == true ]] || { echo 'Traefik must require authentication' >&2; exit 1; }
curl_options+=(--fail --user 'demo-test:test-only-not-a-real-credential')
curl "${curl_options[@]}" "$test_url/api/health/ready" | \
  python3 -c 'import json,sys; assert json.load(sys.stdin)["status"] == "ready"'
curl "${curl_options[@]}" "$test_url/api/version" | python3 -c '
import json, os, sys
version = json.load(sys.stdin)
assert version["git_sha"] == os.environ["PHOTOLAMP_DEPLOY_SHA"]
assert version["api_git_sha"] == os.environ["PHOTOLAMP_DEPLOY_SHA"]
'
for path in / /r/demo-ufa /r/demo-kazan; do curl "${curl_options[@]}" "$test_url$path" >/dev/null; done
[[ "$(curl --silent --show-error --max-time 10 --cacert "$STAGING_TEST_CERT_DIR/cert.pem" \
  -H 'Host: photolamp-test.anliwei.pro' --user 'demo-test:wrong' -o /dev/null -w '%{http_code}' "$test_url/")" == 401 ]]
container_id="$("${compose[@]}" ps -q web)"
docker inspect --format '{{ index .Config.Labels "traefik.http.middlewares.photolamp-test-auth.basicauth.users" }}' \
  "$container_id" | python3 -c '
import os, sys
assert sys.stdin.read().strip() == os.environ["TRAEFIK_BASIC_AUTH_USERS"]
'
echo 'Staging edge passed: verified TLS, Basic Auth 401/200, previews, intact bcrypt and exact web/API SHA'

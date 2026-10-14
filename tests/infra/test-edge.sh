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
export POSTGRES_RUNTIME_PASSWORD
POSTGRES_RUNTIME_PASSWORD="$(openssl rand -hex 32)"
export BACKUP_ACTIVE_KEY_ID=v1
export BACKUP_ENCRYPTION_KEYS
backup_key_v1="$(openssl rand -hex 32)"
BACKUP_ENCRYPTION_KEYS="{\"v1\":\"$backup_key_v1\"}"
export CONTACT_ACTIVE_KEY_VERSION=1
export CONTACT_ENCRYPTION_KEYS
contact_key_v1="$(openssl rand -base64 32 | tr '+/' '-_')"
CONTACT_ENCRYPTION_KEYS="{\"1\":\"$contact_key_v1\"}"
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
# Verify that the shared network really contains an unrelated API and that
# frontend readiness still reaches only Photolamp through its private alias.
"${compose[@]}" exec -T web node -e '
(async () => {
  const response = await fetch("http://test-other-api:8000/api/v1/health/ready");
  if (response.status !== 404 || await response.text() !== "unrelated-api") process.exit(1);
  const ready = await fetch(process.env.API_INTERNAL_URL + "/api/v1/health/ready");
  if (!ready.ok || (await ready.json()).status !== "ready") process.exit(1);
})().catch(() => process.exit(1));
'
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
request_headers="$test_dir/request-headers"
curl "${curl_options[@]}" -H 'X-Request-ID: edge-check-12345678' \
  --dump-header "$request_headers" "$test_url/api/health/ready" | \
  python3 -c 'import json,sys; assert json.load(sys.stdin)["status"] == "ready"'
tr -d '\r' < "$request_headers" | \
  grep -Eiq '^x-request-id:[[:space:]]*edge-check-12345678$'
[[ "$(curl "${curl_options[@]}" -o /dev/null -w '%{http_code}' \
  "$test_url/internal/metrics" 2>/dev/null || true)" == 404 ]]
"${compose[@]}" exec -T api python -c '
import os, urllib.request
body = urllib.request.urlopen("http://127.0.0.1:8000/internal/metrics", timeout=5).read().decode()
expected = os.environ["PHOTOLAMP_DEPLOY_SHA"]
assert "photolamp_http_requests_total" in body
assert f"revision=\"{expected}\"" in body
assert "demo-ufa" not in body and "demo-kazan" not in body
'
curl "${curl_options[@]}" "$test_url/api/version" | python3 -c '
import json, os, sys
version = json.load(sys.stdin)
assert version["git_sha"] == os.environ["PHOTOLAMP_DEPLOY_SHA"]
assert version["api_git_sha"] == os.environ["PHOTOLAMP_DEPLOY_SHA"]
'
for path in / /admin /r/demo-ufa /r/demo-kazan; do curl "${curl_options[@]}" "$test_url$path" >/dev/null; done
cookie_jar="$test_dir/admin-cookies"
readonly public_origin='https://photolamp-test.anliwei.pro'
[[ "$(curl "${curl_options[@]}" -X POST -H 'Origin: https://attacker.invalid' \
  -o /dev/null -w '%{http_code}' "$test_url/api/admin/session" 2>/dev/null || true)" == 403 ]]
curl "${curl_options[@]}" -X POST -H "Origin: $public_origin" \
  -c "$cookie_jar" "$test_url/api/admin/session" | python3 -c '
import json, sys
session = json.load(sys.stdin)
assert session == {"authenticated": True, "demo_only": True}
'
curl "${curl_options[@]}" -b "$cookie_jar" "$test_url/api/admin/session" | python3 -c '
import json, sys
memberships = json.load(sys.stdin)
assert {item["slug"] for item in memberships} == {"demo-ufa", "demo-kazan"}
'
curl "${curl_options[@]}" -X DELETE -H "Origin: $public_origin" \
  -b "$cookie_jar" "$test_url/api/admin/session" >/dev/null

# Exercise the isolated maintenance service and a real key-version transition.
"${compose[@]}" --profile maintenance run --rm --no-deps contact-maintenance --dry-run | \
  python3 -c 'import json,sys; assert json.load(sys.stdin)["target_key_version"] == 1'
CONTACT_ACTIVE_KEY_VERSION=2
contact_key_v2="$(openssl rand -base64 32 | tr '+/' '-_')"
CONTACT_ENCRYPTION_KEYS="$(printf '{\"1\":\"%s\",\"2\":\"%s\"}' \
  "$contact_key_v1" "$contact_key_v2")"
export CONTACT_ACTIVE_KEY_VERSION CONTACT_ENCRYPTION_KEYS
"${compose[@]}" --profile maintenance run --rm --no-deps contact-maintenance --target-version 2 | \
  python3 -c 'import json,sys; data=json.load(sys.stdin); assert data["remaining_non_active"] == 0'

backup_dir="$test_dir/backups"
backup_env="$test_dir/backup.env"
mkdir -m 0700 "$backup_dir"
{
  printf 'PHOTOLAMP_DEPLOY_SHA=%s\n' "$PHOTOLAMP_DEPLOY_SHA"
  printf 'POSTGRES_PASSWORD=%s\n' "$POSTGRES_PASSWORD"
  printf 'POSTGRES_RUNTIME_PASSWORD=%s\n' "$POSTGRES_RUNTIME_PASSWORD"
  printf 'TRAEFIK_BASIC_AUTH_USERS=\047%s\047\n' "$TRAEFIK_BASIC_AUTH_USERS"
  printf 'BACKUP_ACTIVE_KEY_ID=%s\n' "$BACKUP_ACTIVE_KEY_ID"
  printf 'BACKUP_ENCRYPTION_KEYS=\047%s\047\n' "$BACKUP_ENCRYPTION_KEYS"
  printf 'CONTACT_ACTIVE_KEY_VERSION=%s\n' "$CONTACT_ACTIVE_KEY_VERSION"
  printf 'CONTACT_ENCRYPTION_KEYS=\047%s\047\n' "$CONTACT_ENCRYPTION_KEYS"
  printf 'STAGING_TEST_EDGE_NETWORK=%s\n' "$STAGING_TEST_EDGE_NETWORK"
  printf 'STAGING_TEST_CERT_DIR=%s\n' "$STAGING_TEST_CERT_DIR"
  printf 'STAGING_TEST_DATA_DIR=%s\n' "$STAGING_TEST_DATA_DIR"
  printf 'STAGING_TEST_NO_PROXY=%s\n' "$STAGING_TEST_NO_PROXY"
  printf 'CODEX_PROXY_CERT=%s\n' "${CODEX_PROXY_CERT:-/etc/ssl/certs/ca-certificates.crt}"
} > "$backup_env"
chmod 600 "$backup_env"
compose_paths="$PWD/compose.yaml:$PWD/compose.staging.yaml"
if [[ "${PHOTOLAMP_CLOUD_BUILD:-false}" == true ]]; then
  compose_paths="$compose_paths:$PWD/infra/compose.cloud.yaml"
fi
compose_paths="$compose_paths:$PWD/tests/infra/compose.edge.yaml"
backup_command=(env PHOTOLAMP_APP_DIR="$PWD" PHOTOLAMP_ENV_FILE="$backup_env"
  PHOTOLAMP_BACKUP_DIR="$backup_dir" PHOTOLAMP_COMPOSE_FILES="$compose_paths"
  COMPOSE_PROJECT_NAME="$STAGING_TEST_PROJECT" PHOTOLAMP_DEPLOY_SHA="$PHOTOLAMP_DEPLOY_SHA")
"${backup_command[@]}" bash infra/scripts/backup-database.sh
first_archive="$(find "$backup_dir" -maxdepth 1 -type f -name 'photolamp-*-v1-*.dump.plb' -print -quit)"
[[ -n "$first_archive" && -f "$first_archive.sha256" ]]

BACKUP_ACTIVE_KEY_ID=v2
BACKUP_ENCRYPTION_KEYS="{\"v1\":\"$backup_key_v1\",\"v2\":\"$(openssl rand -hex 32)\"}"
python3 - "$backup_env" "$BACKUP_ENCRYPTION_KEYS" <<'PY'
import re, sys
path, keyring = sys.argv[1:]
content = open(path).read()
content = re.sub(r"^BACKUP_ACTIVE_KEY_ID=.*$", "BACKUP_ACTIVE_KEY_ID=v2", content, flags=re.M)
content = re.sub(r"^BACKUP_ENCRYPTION_KEYS=.*$", f"BACKUP_ENCRYPTION_KEYS='{keyring}'", content, flags=re.M)
open(path, "w").write(content)
PY
"${backup_command[@]}" bash infra/scripts/backup-database.sh
"${backup_command[@]}" bash infra/scripts/verify-backup.sh "$first_archive"
[[ "$(find "$backup_dir" -maxdepth 1 -type f -name 'photolamp-*.dump.plb' | wc -l)" == 2 ]]

[[ "$(curl --silent --show-error --max-time 10 --cacert "$STAGING_TEST_CERT_DIR/cert.pem" \
  -H 'Host: photolamp-test.anliwei.pro' --user 'demo-test:wrong' -o /dev/null -w '%{http_code}' "$test_url/")" == 401 ]]
container_id="$("${compose[@]}" ps -q web)"
docker inspect --format '{{ index .Config.Labels "traefik.http.middlewares.photolamp-test-auth.basicauth.users" }}' \
  "$container_id" | python3 -c '
import os, sys
assert sys.stdin.read().strip() == os.environ["TRAEFIK_BASIC_AUTH_USERS"]
'
echo 'Staging edge passed: TLS, auth, request IDs, private metrics, catalogs, admin, contact rotation, backup restore and exact SHA'

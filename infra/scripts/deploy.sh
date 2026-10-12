#!/usr/bin/env bash
set -Eeuo pipefail
readonly app_dir=/opt/photolamp-test
readonly env_file="$app_dir/.env"
readonly hostname=photolamp-test.anliwei.pro
readonly compose=(docker compose -p photolamp-test --env-file "$env_file" -f compose.yaml -f compose.staging.yaml)
fail() { printf '[photolamp-deploy] %s\n' "$*" >&2; exit 1; }
on_error() {
  local code="$?"
  trap - ERR
  printf '[photolamp-deploy] Deployment failed; existing data is preserved\n' >&2
  "${compose[@]}" ps >&2 || true
  exit "$code"
}
trap on_error ERR
[[ "$PWD" == "$app_dir" && ! -L "$app_dir" ]] || fail 'Run from the staging checkout'
[[ -f "$env_file" && ! -L "$env_file" ]] || fail 'Server .env is missing or unsafe'
[[ "$(stat -c '%u:%a' "$env_file")" == "$(id -u):600" ]] || fail '.env must belong to the deploy user and have mode 600'
[[ "${PHOTOLAMP_DEPLOY_SHA:-}" =~ ^[a-f0-9]{40}$ ]] || fail 'Set an exact CI-tested PHOTOLAMP_DEPLOY_SHA'
[[ "$(git rev-parse HEAD)" == "$PHOTOLAMP_DEPLOY_SHA" ]] || fail 'Source does not match requested SHA'
[[ -d /mnt/raid/photolamp-test/postgres && ! -L /mnt/raid/photolamp-test/postgres ]] || fail 'Run bootstrap-host.sh first'
[[ "$(stat -c '%u:%g:%a' /mnt/raid/photolamp-test/postgres)" == '999:999:700' ]] || fail 'PostgreSQL directory needs ownership 999:999 and mode 700'
docker network inspect traefik >/dev/null 2>&1 || fail 'Existing Traefik network is required'
"${compose[@]}" config --format json | python3 infra/scripts/check-staging-config.py

read_env() {
  local line value
  line="$(awk -v key="$1" 'index($0,key"=")==1 {print;exit}' "$env_file")"
  value="${line#*=}"
  if [[ "$value" == \"*\" || "$value" == \'*\' ]]; then value="${value:1:${#value}-2}"; fi
  [[ -n "$value" && "$value" != REPLACE_* ]] || fail "Configure $1 in server .env"
  printf '%s' "$value"
}
auth_user="$(read_env PHOTOLAMP_HEALTHCHECK_USER)"
auth_password="$(read_env PHOTOLAMP_HEALTHCHECK_PASSWORD)"
[[ "$auth_user" =~ ^[a-zA-Z0-9_.-]+$ && ${#auth_password} -ge 16 ]] || fail 'Configure healthcheck Basic Auth credentials'

# Built images keep the revision in their ENV and OCI label; runtime cannot silently retag it.
"${compose[@]}" build --pull
"${compose[@]}" up -d --wait --wait-timeout 90 postgres redis
"${compose[@]}" up -d --wait --wait-timeout 180

"${compose[@]}" exec -T web node -e \
  "fetch('http://127.0.0.1:3000/api/health/ready').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"

authenticated_get() {
  local escaped_password="${auth_password//\\/\\\\}"
  escaped_password="${escaped_password//\"/\\\"}"
  printf 'user = "%s:%s"\n' "$auth_user" "$escaped_password" | \
    curl -q --config - --fail --silent --show-error --max-time 12 "https://$hostname$1"
}
ready=false
for attempt in {1..18}; do
  if body="$(authenticated_get /api/health/ready)" && \
    python3 -c 'import json,sys; sys.exit(json.load(sys.stdin).get("status") != "ready")' <<< "$body"; then
    ready=true; break
  fi
  printf '[photolamp-deploy] Waiting for HTTPS readiness (%s/18)\n' "$attempt"
  sleep 5
done
[[ "$ready" == true ]] || fail 'External HTTPS readiness failed'
authenticated_get /api/version | python3 -c '
import json, sys
data = json.load(sys.stdin)
expected = sys.argv[1]
if data.get("git_sha") != expected or data.get("api_git_sha") != expected:
    raise SystemExit("Deployed web/API do not match the CI-tested revision")
' "$PHOTOLAMP_DEPLOY_SHA"
unset auth_user auth_password body
printf '[photolamp-deploy] Healthy release %s at https://%s\n' "$PHOTOLAMP_DEPLOY_SHA" "$hostname"

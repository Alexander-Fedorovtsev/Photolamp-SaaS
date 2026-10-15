#!/usr/bin/env bash
set -Eeuo pipefail

fail() { printf '[photolamp-monitor] %s\n' "$*" >&2; exit 1; }

readonly app_dir="${PHOTOLAMP_APP_DIR:-/opt/photolamp-test}"
readonly env_file="${PHOTOLAMP_ENV_FILE:-$app_dir/.env}"
readonly backup_dir="${PHOTOLAMP_BACKUP_DIR:-/mnt/raid/photolamp-test/backups}"
readonly project_name="${COMPOSE_PROJECT_NAME:-photolamp-test}"
readonly hostname="${PHOTOLAMP_HOSTNAME:-photolamp-test.anliwei.pro}"
readonly maximum_backup_age_seconds="${PHOTOLAMP_MAX_BACKUP_AGE_SECONDS:-93600}"

[[ "${PHOTOLAMP_DEPLOY_SHA:-}" =~ ^[a-f0-9]{40}$ ]] || fail 'Set an exact expected Git SHA'
[[ -d "$app_dir/.git" && ! -L "$app_dir" ]] || fail 'Application checkout is missing or unsafe'
cd "$app_dir"
[[ "$(git rev-parse HEAD)" == "$PHOTOLAMP_DEPLOY_SHA" ]] || fail 'Staging revision differs from the monitored revision'
[[ -f "$env_file" && ! -L "$env_file" ]] || fail 'Server .env is missing or unsafe'
[[ "$(stat -c '%u:%a' "$env_file")" == "$(id -u):600" ]] || fail '.env must belong to the deploy user and have mode 600'
[[ -d "$backup_dir" && ! -L "$backup_dir" ]] || fail 'Backup storage is missing or unsafe'
[[ "$maximum_backup_age_seconds" =~ ^[0-9]+$ ]] || fail 'Backup age threshold must be an integer'

if [[ "${PHOTOLAMP_MAINTENANCE_LOCK_HELD:-false}" != true ]]; then
  exec 9> .deploy.lock
  flock -w 600 9 || fail 'Deployment or another maintenance task is still running'
fi

readonly compose=(
  docker compose -p "$project_name" --env-file "$env_file"
  -f compose.yaml -f compose.staging.yaml
)

for service in postgres redis api web; do
  container="$("${compose[@]}" ps -q "$service")"
  [[ -n "$container" ]] || fail "Service $service has no container"
  state="$(docker inspect --format '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' "$container")"
  [[ "$state" == 'running healthy' ]] || fail "Service $service is not healthy"
done

metrics="$("${compose[@]}" exec -T api python -c '
import urllib.request
with urllib.request.urlopen("http://127.0.0.1:8000/internal/metrics", timeout=5) as response:
    if response.status != 200:
        raise SystemExit("metrics endpoint is unavailable")
    print(response.read().decode("utf-8"), end="")
')"
grep -q '^photolamp_http_requests_total' <<< "$metrics" || fail 'HTTP request metrics are missing'
grep -q '^photolamp_http_request_duration_seconds' <<< "$metrics" || fail 'HTTP duration metrics are missing'
grep -q "revision=\"$PHOTOLAMP_DEPLOY_SHA\"" <<< "$metrics" || fail 'Metrics build revision is stale'
[[ "$metrics" != *demo-ufa* && "$metrics" != *demo-kazan* ]] || fail 'Metrics contain tenant values'
unset metrics

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
[[ "$auth_user" =~ ^[a-zA-Z0-9_.-]+$ && ${#auth_password} -ge 16 ]] || fail 'Healthcheck Basic Auth credentials are invalid'
request_id="monitor-${PHOTOLAMP_DEPLOY_SHA:0:16}"
headers_file="$(mktemp)"
trap 'rm -f -- "$headers_file"' EXIT

authenticated_get() {
  local path="$1"
  local escaped_password="${auth_password//\\/\\\\}"
  escaped_password="${escaped_password//\"/\\\"}"
  printf 'user = "%s:%s"\n' "$auth_user" "$escaped_password" | \
    curl -q --config - --fail --silent --show-error --max-time 12 \
      --header "X-Request-ID: $request_id" --dump-header "$headers_file" \
      "https://$hostname$path"
}

ready_body="$(authenticated_get /api/health/ready)"
python3 -c '
import json, sys
if json.load(sys.stdin) != {"status": "ready"}:
    raise SystemExit("Public readiness response is invalid")
' <<< "$ready_body"
tr -d '\r' < "$headers_file" | \
  grep -Eiq "^x-request-id:[[:space:]]*$request_id$" || fail 'Public readiness lost its request ID'

version_body="$(authenticated_get /api/version)"
python3 -c '
import json, sys
data = json.load(sys.stdin)
expected = sys.argv[1]
if data.get("git_sha") != expected or data.get("api_git_sha") != expected:
    raise SystemExit("Public web/API revision is stale")
' "$PHOTOLAMP_DEPLOY_SHA" <<< "$version_body"

escaped_password="${auth_password//\\/\\\\}"
escaped_password="${escaped_password//\"/\\\"}"
metrics_status="$(printf 'user = "%s:%s"\n' "$auth_user" "$escaped_password" | \
  curl -q --config - --silent --show-error --output /dev/null --write-out '%{http_code}' \
    --max-time 12 "https://$hostname/internal/metrics")"
[[ "$metrics_status" == 404 ]] || fail 'Internal metrics unexpectedly reached the public edge'
unset auth_user auth_password escaped_password ready_body version_body

latest_record="$(find "$backup_dir" -maxdepth 1 -type f -name 'photolamp-*.dump.plb' \
  -printf '%T@ %p\n' | sort -n | tail -1)"
[[ -n "$latest_record" ]] || fail 'No encrypted backup archive exists'
latest_archive="${latest_record#* }"
[[ -f "$latest_archive.sha256" && ! -L "$latest_archive.sha256" ]] || fail 'Latest backup checksum is missing or unsafe'
(cd "$backup_dir" && sha256sum --check --status "$(basename "$latest_archive").sha256") || fail 'Latest backup checksum failed'
backup_age_seconds=$(( $(date +%s) - $(stat -c '%Y' "$latest_archive") ))
((backup_age_seconds >= 0 && backup_age_seconds <= maximum_backup_age_seconds)) || fail 'Latest encrypted backup is stale'
PHOTOLAMP_APP_DIR="$app_dir" \
PHOTOLAMP_ENV_FILE="$env_file" \
PHOTOLAMP_BACKUP_DIR="$backup_dir" \
PHOTOLAMP_MAINTENANCE_LOCK_HELD=true \
PHOTOLAMP_DEPLOY_SHA="$PHOTOLAMP_DEPLOY_SHA" \
COMPOSE_PROJECT_NAME="$project_name" \
  bash infra/scripts/sync-offsite-backup.sh --verify-only "$latest_archive"
disk_used_percent="$(df -P "$backup_dir" | awk 'NR==2 {gsub(/%/, "", $5); print $5}')"
[[ "$disk_used_percent" =~ ^[0-9]+$ ]] || fail 'Cannot read backup storage utilization'
((disk_used_percent < 90)) || fail 'Backup storage utilization reached 90 percent'

rm -f -- "$headers_file"
trap - EXIT
printf '[photolamp-monitor] Healthy revision %s; backup age %ss; storage %s%% used\n' \
  "$PHOTOLAMP_DEPLOY_SHA" "$backup_age_seconds" "$disk_used_percent"

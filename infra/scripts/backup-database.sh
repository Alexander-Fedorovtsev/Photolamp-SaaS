#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

fail() { printf '[photolamp-backup] %s\n' "$*" >&2; exit 1; }

readonly app_dir="${PHOTOLAMP_APP_DIR:-/opt/photolamp-test}"
readonly env_file="${PHOTOLAMP_ENV_FILE:-$app_dir/.env}"
readonly backup_dir="${PHOTOLAMP_BACKUP_DIR:-/mnt/raid/photolamp-test/backups}"
readonly project_name="${COMPOSE_PROJECT_NAME:-photolamp-test}"
readonly compose_files="${PHOTOLAMP_COMPOSE_FILES:-compose.yaml:compose.staging.yaml}"

[[ -d "$app_dir/.git" && ! -L "$app_dir" ]] || fail 'Application checkout is missing or unsafe'
cd "$app_dir"
[[ -f "$env_file" && ! -L "$env_file" ]] || fail 'Server .env is missing or unsafe'
[[ "$(stat -c '%u:%a' "$env_file")" == "$(id -u):600" ]] || fail '.env must belong to the deploy user and have mode 600'
[[ -d "$backup_dir" && ! -L "$backup_dir" ]] || fail 'Backup storage is missing or unsafe'
[[ "$(stat -c '%u:%g:%a' "$backup_dir")" == "$(id -u):$(id -g):700" ]] || fail 'Backup storage must belong to the deploy user and have mode 700'

if [[ "${PHOTOLAMP_MAINTENANCE_LOCK_HELD:-false}" != true ]]; then
  exec 9> .deploy.lock
  flock -w 1800 9 || fail 'Deployment or another maintenance task is still running'
fi

read_env() {
  local line value
  line="$(awk -v key="$1" 'index($0,key"=")==1 {print;exit}' "$env_file")"
  value="${line#*=}"
  if [[ "$value" == \"*\" || "$value" == \'*\' ]]; then value="${value:1:${#value}-2}"; fi
  [[ -n "$value" ]] || fail "Configure $1 in server .env"
  printf '%s' "$value"
}

read_optional_env() {
  local line value
  line="$(awk -v key="$1" 'index($0,key"=")==1 {print;exit}' "$env_file")"
  [[ -n "$line" ]] || return 0
  value="${line#*=}"
  if [[ "$value" == \"*\" || "$value" == \'*\' ]]; then value="${value:1:${#value}-2}"; fi
  printf '%s' "$value"
}

active_key_id="$(read_env BACKUP_ACTIVE_KEY_ID)"
[[ "$active_key_id" =~ ^[a-z0-9][a-z0-9_-]{0,31}$ ]] || fail 'Backup active key ID is invalid'
retention_days="${BACKUP_RETENTION_DAYS:-$(read_optional_env BACKUP_RETENTION_DAYS)}"
retention_count="${BACKUP_RETENTION_COUNT:-$(read_optional_env BACKUP_RETENTION_COUNT)}"
retention_days="${retention_days:-30}"
retention_count="${retention_count:-35}"
if [[ ! "$retention_days" =~ ^[0-9]+$ ]] || ((retention_days < 7 || retention_days > 365)); then
  fail 'BACKUP_RETENTION_DAYS must be between 7 and 365'
fi
if [[ ! "$retention_count" =~ ^[0-9]+$ ]] || ((retention_count < 7 || retention_count > 400)); then
  fail 'BACKUP_RETENTION_COUNT must be between 7 and 400'
fi

export PHOTOLAMP_DEPLOY_SHA="${PHOTOLAMP_DEPLOY_SHA:-$(git rev-parse HEAD)}"
[[ "$PHOTOLAMP_DEPLOY_SHA" =~ ^[a-f0-9]{40}$ ]] || fail 'An exact deployed Git SHA is required'
readonly compose=(docker compose -p "$project_name" --env-file "$env_file")
IFS=: read -r -a file_list <<< "$compose_files"
compose_command=("${compose[@]}")
for file in "${file_list[@]}"; do
  [[ -n "$file" && -f "$file" ]] || fail 'A configured Compose file is missing'
  compose_command+=(-f "$file")
done

timestamp="$(date -u +'%Y%m%dT%H%M%SZ')"
suffix="$(openssl rand -hex 3)"
archive_name="photolamp-${timestamp}-${active_key_id}-${suffix}.dump.plb"
archive="$backup_dir/$archive_name"
temporary="$backup_dir/.${archive_name}.partial"
checksum="$archive.sha256"
checksum_temporary="$backup_dir/.${archive_name}.sha256.partial"
completed=false
cleanup() {
  rm -f -- "$temporary" "$checksum_temporary"
  if [[ "$completed" != true ]]; then rm -f -- "$archive" "$checksum"; fi
}
trap cleanup EXIT

# Variables in this command expand inside the PostgreSQL container.
# shellcheck disable=SC2016
"${compose_command[@]}" exec -T postgres sh -ceu \
  'pg_dump --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --format=custom --compress=9 --no-owner --no-acl' | \
  "${compose_command[@]}" run --rm -T --no-deps backup-crypto encrypt > "$temporary"
[[ -s "$temporary" ]] || fail 'Encrypted backup archive is empty'
chmod 600 "$temporary"
sync -f "$temporary"
mv -- "$temporary" "$archive"
(cd "$backup_dir" && sha256sum "$archive_name") > "$checksum_temporary"
chmod 600 "$checksum_temporary"
sync -f "$checksum_temporary"
mv -- "$checksum_temporary" "$checksum"

PHOTOLAMP_APP_DIR="$app_dir" \
PHOTOLAMP_ENV_FILE="$env_file" \
PHOTOLAMP_BACKUP_DIR="$backup_dir" \
PHOTOLAMP_COMPOSE_FILES="$compose_files" \
PHOTOLAMP_MAINTENANCE_LOCK_HELD=true \
COMPOSE_PROJECT_NAME="$project_name" \
  bash infra/scripts/verify-backup.sh "$archive"

while IFS= read -r -d '' old_archive; do
  [[ "$old_archive" == "$archive" ]] && continue
  rm -f -- "$old_archive" "$old_archive.sha256"
done < <(find "$backup_dir" -maxdepth 1 -type f -name 'photolamp-*.dump.plb' \
  -mtime "+$retention_days" -print0)

mapfile -d '' archives < <(find "$backup_dir" -maxdepth 1 -type f \
  -name 'photolamp-*.dump.plb' -print0 | sort -z)
if ((${#archives[@]} > retention_count)); then
  remove_count=$((${#archives[@]} - retention_count))
  for ((index = 0; index < remove_count; index += 1)); do
    [[ "${archives[$index]}" == "$archive" ]] && continue
    rm -f -- "${archives[$index]}" "${archives[$index]}.sha256"
  done
fi

completed=true
trap - EXIT
printf '[photolamp-backup] Encrypted backup and restore drill passed: %s\n' "$archive_name"

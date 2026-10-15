#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

fail() { printf '[photolamp-offsite] %s\n' "$*" >&2; exit 1; }

mode=sync
if [[ $# -eq 2 && "$1" == --verify-only ]]; then
  mode=verify
  shift
fi
[[ $# -eq 1 ]] || fail 'Usage: sync-offsite-backup.sh [--verify-only] ARCHIVE'
readonly app_dir="${PHOTOLAMP_APP_DIR:-/opt/photolamp-test}"
readonly env_file="${PHOTOLAMP_ENV_FILE:-$app_dir/.env}"
readonly backup_dir="${PHOTOLAMP_BACKUP_DIR:-/mnt/raid/photolamp-test/backups}"
readonly project_name="${COMPOSE_PROJECT_NAME:-photolamp-test}"
readonly compose_files="${PHOTOLAMP_COMPOSE_FILES:-compose.yaml:compose.staging.yaml}"

[[ -d "$app_dir/.git" && ! -L "$app_dir" ]] || fail 'Application checkout is missing or unsafe'
cd "$app_dir"
[[ -f "$env_file" && ! -L "$env_file" ]] || fail 'Server .env is missing or unsafe'
[[ "$(stat -c '%u:%a' "$env_file")" == "$(id -u):600" ]] || fail '.env must belong to the deploy user and have mode 600'

read_optional_env() {
  local line value
  line="$(awk -v key="$1" 'index($0,key"=")==1 {print;exit}' "$env_file")"
  [[ -n "$line" ]] || return 0
  value="${line#*=}"
  if [[ "$value" == \"*\" || "$value" == \'*\' ]]; then value="${value:1:${#value}-2}"; fi
  printf '%s' "$value"
}

enabled="$(read_optional_env OFFSITE_BACKUP_ENABLED)"
enabled="${enabled:-false}"
[[ "$enabled" == true || "$enabled" == false ]] || fail 'OFFSITE_BACKUP_ENABLED must be true or false'
if [[ "$enabled" != true ]]; then
  printf '[photolamp-offsite] External backup is disabled; local encrypted backup remains authoritative\n'
  exit 0
fi

archive="$(realpath -e -- "$1")"
canonical_backup_dir="$(realpath -e -- "$backup_dir")"
[[ "$archive" == "$canonical_backup_dir"/photolamp-*.dump.plb ]] || fail 'Archive must be inside the configured backup directory'
[[ -f "$archive" && ! -L "$archive" ]] || fail 'Encrypted archive is missing or unsafe'
checksum_file="$archive.sha256"
[[ -f "$checksum_file" && ! -L "$checksum_file" ]] || fail 'Encrypted archive checksum is missing or unsafe'
(cd "$canonical_backup_dir" && sha256sum --check --status "$(basename "$checksum_file")") || fail 'Encrypted archive checksum verification failed'

checksum="$(awk 'NR == 1 {print $1}' "$checksum_file")"
[[ "$checksum" =~ ^[a-f0-9]{64}$ ]] || fail 'Encrypted archive checksum is invalid'
archive_name="$(basename "$archive")"
archive_size="$(stat -c '%s' "$archive")"
[[ "$archive_size" =~ ^[0-9]+$ && "$archive_size" -gt 0 ]] || fail 'Encrypted archive size is invalid'
revision="${PHOTOLAMP_DEPLOY_SHA:-$(git rev-parse HEAD)}"
[[ "$revision" =~ ^[a-f0-9]{40}$ ]] || fail 'An exact deployed Git SHA is required'
lock_days="$(read_optional_env OFFSITE_BACKUP_OBJECT_LOCK_DAYS)"
lock_days="${lock_days:-30}"
[[ "$lock_days" =~ ^[0-9]+$ ]] || fail 'Object-lock duration must be an integer'
((lock_days >= 7 && lock_days <= 365)) || fail 'Object-lock duration must be between 7 and 365 days'
if [[ "$mode" == verify ]]; then
  archive_mtime="$(stat -c '%Y' "$archive")"
  minimum_retain_epoch=$((archive_mtime + lock_days * 86400 - 600))
  retain_until="$(date -u -d "@$minimum_retain_epoch" +'%Y-%m-%dT%H:%M:%SZ')"
else
  retain_until="$(date -u -d "+$lock_days days" +'%Y-%m-%dT%H:%M:%SZ')"
fi

readonly compose=(docker compose -p "$project_name" --env-file "$env_file")
IFS=: read -r -a file_list <<< "$compose_files"
compose_command=("${compose[@]}")
for file in "${file_list[@]}"; do
  [[ -n "$file" && -f "$file" ]] || fail 'A configured Compose file is missing'
  compose_command+=(-f "$file")
done

if [[ "$mode" == verify ]]; then
  "${compose_command[@]}" run --rm -T --no-deps offsite-backup verify \
    --archive-name "$archive_name" --checksum "$checksum" --revision "$revision" \
    --retain-until "$retain_until"
  exit 0
fi

"${compose_command[@]}" run --rm -T --no-deps offsite-backup upload \
  --archive-name "$archive_name" --checksum "$checksum" --revision "$revision" \
  --size "$archive_size" --retain-until "$retain_until" < "$archive"

restore_dir="$(mktemp -d "$canonical_backup_dir/.offsite-restore.XXXXXX")"
cleanup() { rm -rf -- "$restore_dir"; }
trap cleanup EXIT
downloaded="$restore_dir/$archive_name"
"${compose_command[@]}" run --rm -T --no-deps offsite-backup download \
  --archive-name "$archive_name" --checksum "$checksum" --revision "$revision" \
  --retain-until "$retain_until" > "$downloaded"
chmod 600 "$downloaded"
printf '%s  %s\n' "$checksum" "$archive_name" > "$downloaded.sha256"
chmod 600 "$downloaded.sha256"

PHOTOLAMP_APP_DIR="$app_dir" \
PHOTOLAMP_ENV_FILE="$env_file" \
PHOTOLAMP_BACKUP_DIR="$backup_dir" \
PHOTOLAMP_COMPOSE_FILES="$compose_files" \
PHOTOLAMP_MAINTENANCE_LOCK_HELD=true \
COMPOSE_PROJECT_NAME="$project_name" \
  bash infra/scripts/verify-backup.sh "$downloaded"

cleanup
trap - EXIT
printf '[photolamp-offsite] Remote download and disposable restore drill passed: %s\n' "$archive_name"

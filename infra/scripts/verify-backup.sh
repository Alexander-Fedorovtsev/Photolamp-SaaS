#!/usr/bin/env bash
set -Eeuo pipefail

fail() { printf '[photolamp-restore] %s\n' "$*" >&2; exit 1; }

[[ $# -eq 1 ]] || fail 'Usage: verify-backup.sh ARCHIVE'
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

archive="$(realpath -e -- "$1")"
canonical_backup_dir="$(realpath -e -- "$backup_dir")"
case "$archive" in
  "$canonical_backup_dir"/photolamp-*.dump.plb) ;;
  "$canonical_backup_dir"/.offsite-restore.*/photolamp-*.dump.plb)
    restore_source_dir="$(dirname "$archive")"
    [[ "$(dirname "$restore_source_dir")" == "$canonical_backup_dir" ]] || fail 'Off-site restore directory is unsafe'
    [[ "$(basename "$restore_source_dir")" == .offsite-restore.* ]] || fail 'Off-site restore directory is unsafe'
    [[ ! -L "$restore_source_dir" && "$(stat -c '%u:%a' "$restore_source_dir")" == "$(id -u):700" ]] || fail 'Off-site restore directory is unsafe'
    ;;
  *) fail 'Archive must be inside the configured backup directory' ;;
esac
[[ -f "$archive" && ! -L "$archive" ]] || fail 'Backup archive is missing or unsafe'
[[ "$(stat -c '%u:%a' "$archive")" == "$(id -u):600" ]] || fail 'Backup archive must belong to the deploy user and have mode 600'
checksum="$archive.sha256"
[[ -f "$checksum" && ! -L "$checksum" ]] || fail 'Backup checksum is missing or unsafe'
[[ "$(stat -c '%u:%a' "$checksum")" == "$(id -u):600" ]] || fail 'Backup checksum must belong to the deploy user and have mode 600'
(cd "$(dirname "$archive")" && sha256sum --check --status "$(basename "$checksum")") || fail 'Backup checksum verification failed'

if [[ "${PHOTOLAMP_MAINTENANCE_LOCK_HELD:-false}" != true ]]; then
  exec 9> .deploy.lock
  flock -w 1800 9 || fail 'Deployment or another maintenance task is still running'
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

restore_database="photolamp_restore_$(date -u +'%Y%m%d_%H%M%S')_$(openssl rand -hex 2)"
drop_restore_database() {
  # Variables in these commands expand inside the PostgreSQL container.
  # shellcheck disable=SC2016
  "${compose_command[@]}" exec -T postgres sh -ceu \
    'dropdb --username "$POSTGRES_USER" --if-exists --force "$1"' sh \
    "$restore_database" >/dev/null 2>&1 || true
}
trap drop_restore_database EXIT
# shellcheck disable=SC2016
"${compose_command[@]}" exec -T postgres sh -ceu \
  'createdb --username "$POSTGRES_USER" "$1"' sh "$restore_database"

# shellcheck disable=SC2016
"${compose_command[@]}" run --rm -T --no-deps backup-crypto decrypt < "$archive" | \
  "${compose_command[@]}" exec -T postgres sh -ceu \
    'pg_restore --username "$POSTGRES_USER" --dbname "$1" --format=custom --exit-on-error --no-owner --no-acl' \
    sh "$restore_database"

# shellcheck disable=SC2016
schema_valid="$("${compose_command[@]}" exec -T postgres sh -ceu \
  'psql --username "$POSTGRES_USER" --dbname "$1" --tuples-only --no-align --set ON_ERROR_STOP=1 \
    --command "SELECT (SELECT count(*) = 1 FROM alembic_version) \
      AND to_regclass('"'"'public.tenants'"'"') IS NOT NULL \
      AND to_regclass('"'"'public.orders'"'"') IS NOT NULL \
      AND (SELECT bool_and(c.relrowsecurity AND c.relforcerowsecurity) \
        FROM pg_class c WHERE c.oid IN ('"'"'customer_private_profiles'"'"'::regclass, \
          '"'"'order_private_details'"'"'::regclass));"' \
  sh "$restore_database")"
[[ "$schema_valid" == t ]] || fail 'Restored database failed schema and RLS verification'

drop_restore_database
trap - EXIT
printf '[photolamp-restore] Disposable restore verified and removed: %s\n' "$(basename "$archive")"

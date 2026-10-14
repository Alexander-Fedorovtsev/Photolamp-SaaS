#!/usr/bin/env bash
set -Eeuo pipefail
readonly app_dir=/opt/photolamp-test
readonly env_file="$app_dir/.env"
readonly compose=(docker compose -p photolamp-test --env-file "$env_file" -f compose.yaml -f compose.staging.yaml)
fail() { printf '[photolamp-contact-retirement] %s\n' "$*" >&2; exit 1; }
[[ $# -eq 1 && "$1" =~ ^[1-9][0-9]*$ ]] || fail 'Usage: retire-contact-key.sh VERSION'
readonly version="$1"
[[ "$PWD" == "$app_dir" && ! -L "$app_dir" ]] || fail 'Run from the staging checkout'
[[ -f "$env_file" && ! -L "$env_file" ]] || fail 'Server .env is missing or unsafe'
exec 9> .deploy.lock
flock -w 1800 9 || fail 'Another maintenance operation is still running'
: "${PHOTOLAMP_DEPLOY_SHA:=$(git rev-parse HEAD)}"
export PHOTOLAMP_DEPLOY_SHA
# This database check runs before the atomic .env edit and blocks referenced keys.
"${compose[@]}" --profile maintenance run --rm --no-deps contact-maintenance \
  --assert-unused-version "$version"
python3 infra/scripts/retire-contact-key.py "$env_file" "$version"
"${compose[@]}" up -d --no-deps --wait --wait-timeout 90 api
printf '[photolamp-contact-retirement] Unused version %s was removed\n' "$version"

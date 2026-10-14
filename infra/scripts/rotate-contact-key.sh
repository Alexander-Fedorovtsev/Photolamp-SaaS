#!/usr/bin/env bash
set -Eeuo pipefail
readonly app_dir=/opt/photolamp-test
readonly env_file="$app_dir/.env"
readonly compose=(docker compose -p photolamp-test --env-file "$env_file" -f compose.yaml -f compose.staging.yaml)
fail() { printf '[photolamp-contact-rotation] %s\n' "$*" >&2; exit 1; }
[[ $# -eq 1 && "$1" =~ ^[1-9][0-9]*$ ]] || fail 'Usage: rotate-contact-key.sh NEW_VERSION'
readonly new_version="$1"
[[ "$PWD" == "$app_dir" && ! -L "$app_dir" ]] || fail 'Run from the staging checkout'
[[ -f "$env_file" && ! -L "$env_file" ]] || fail 'Server .env is missing or unsafe'
[[ "$(stat -c '%u:%a' "$env_file")" == "$(id -u):600" ]] || fail '.env must belong to the deploy user and have mode 600'
exec 9> .deploy.lock
flock -w 1800 9 || fail 'Another maintenance operation is still running'
python3 infra/scripts/ensure-contact-secrets.py "$env_file"
python3 infra/scripts/rotate-contact-key.py "$env_file" "$new_version"
: "${PHOTOLAMP_DEPLOY_SHA:=$(git rev-parse HEAD)}"
export PHOTOLAMP_DEPLOY_SHA
"${compose[@]}" --profile maintenance config --format json | python3 infra/scripts/check-staging-config.py
# Restart first so every concurrent application write immediately uses the new key.
"${compose[@]}" up -d --no-deps --wait --wait-timeout 90 api
"${compose[@]}" --profile maintenance run --rm --no-deps contact-maintenance \
  --target-version "$new_version"
printf '[photolamp-contact-rotation] Version %s is active and all contact rows were rotated\n' "$new_version"

#!/usr/bin/env bash
# Sent over SSH by CD. Host credentials are never sent or sourced by this script.
set -Eeuo pipefail
fail() { printf '[photolamp-remote] %s\n' "$*" >&2; exit 1; }
retry_command() {
  local max_attempts="$1"
  local delay_seconds="$2"
  local description="$3"
  shift 3
  [[ "$max_attempts" =~ ^[1-9][0-9]*$ ]] || return 2
  [[ "$delay_seconds" =~ ^[0-9]+$ ]] || return 2
  local attempt=1
  until "$@"; do
    if (( attempt >= max_attempts )); then
      printf '[photolamp-retry] %s failed after %d attempts\n' \
        "$description" "$max_attempts" >&2
      return 1
    fi
    local wait_seconds=$((delay_seconds * attempt))
    printf '[photolamp-retry] %s failed (attempt %d/%d); retrying in %ss\n' \
      "$description" "$attempt" "$max_attempts" "$wait_seconds" >&2
    sleep "$wait_seconds"
    ((attempt += 1))
  done
}
[[ $# -eq 1 && "$1" =~ ^[a-f0-9]{40}$ ]] || fail 'Expected one exact Git SHA'
readonly deploy_sha="$1"
readonly app_dir=/opt/photolamp-test
readonly repository=https://github.com/Alexander-Fedorovtsev/Photolamp-SaaS.git
[[ -d "$app_dir/.git" && ! -L "$app_dir" ]] || fail 'Clone the repository during host setup first'
cd "$app_dir"
[[ "$(git remote get-url origin)" == "$repository" ]] || fail 'Unexpected repository origin'
[[ -f .env && ! -L .env ]] || fail 'Prepare the server .env first'
# Serialize manual and workflow deployments before changing source or containers.
exec 9> .deploy.lock
flock -w 1800 9 || fail 'Another deployment is still running'
[[ -z "$(git status --porcelain --untracked-files=no)" ]] || fail 'Tracked worktree has local changes'
retry_command 5 10 'GitHub fetch' \
  git fetch --no-tags origin '+refs/heads/main:refs/remotes/origin/main'
if [[ "$(git rev-parse origin/main)" != "$deploy_sha" ]]; then
  printf '[photolamp-remote] Skipping outdated CI revision; main has advanced\n'
  exit 0
fi
git checkout --detach "$deploy_sha"
[[ "$(git rev-parse HEAD)" == "$deploy_sha" ]] || fail 'Checkout does not match approved revision'
PHOTOLAMP_DEPLOY_SHA="$deploy_sha" bash infra/scripts/deploy.sh

#!/usr/bin/env bash
# Sent over SSH by the scheduled monitor. Host credentials remain on the host.
set -Eeuo pipefail
fail() { printf '[photolamp-remote-monitor] %s\n' "$*" >&2; exit 1; }
[[ $# -eq 1 && "$1" =~ ^[a-f0-9]{40}$ ]] || fail 'Expected one workflow Git SHA'
readonly expected_sha="$1"
readonly app_dir=/opt/photolamp-test
readonly repository=https://github.com/Alexander-Fedorovtsev/Photolamp-SaaS.git
[[ -d "$app_dir/.git" && ! -L "$app_dir" ]] || fail 'Staging checkout is missing or unsafe'
cd "$app_dir"
[[ "$(git remote get-url origin)" == "$repository" ]] || fail 'Unexpected repository origin'
[[ -z "$(git status --porcelain --untracked-files=no)" ]] || fail 'Tracked worktree has local changes'
exec 9> .deploy.lock
flock -w 600 9 || fail 'Deployment or another maintenance task is still running'
[[ "$(git rev-parse HEAD)" == "$expected_sha" ]] || fail 'Staging has not deployed the monitored main revision'
PHOTOLAMP_DEPLOY_SHA="$expected_sha" PHOTOLAMP_MAINTENANCE_LOCK_HELD=true \
  bash infra/scripts/monitor-staging.sh

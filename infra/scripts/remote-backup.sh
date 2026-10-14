#!/usr/bin/env bash
# Sent over SSH by the scheduled workflow. Backup keys remain on the host.
set -Eeuo pipefail
fail() { printf '[photolamp-remote-backup] %s\n' "$*" >&2; exit 1; }
[[ $# -eq 1 && "$1" =~ ^[a-f0-9]{40}$ ]] || fail 'Expected one workflow Git SHA'
readonly app_dir=/opt/photolamp-test
readonly repository=https://github.com/Alexander-Fedorovtsev/Photolamp-SaaS.git
[[ -d "$app_dir/.git" && ! -L "$app_dir" ]] || fail 'Staging checkout is missing or unsafe'
cd "$app_dir"
[[ "$(git remote get-url origin)" == "$repository" ]] || fail 'Unexpected repository origin'
[[ -z "$(git status --porcelain --untracked-files=no)" ]] || fail 'Tracked worktree has local changes'
deployed_sha="$(git rev-parse HEAD)"
[[ "$deployed_sha" =~ ^[a-f0-9]{40}$ ]] || fail 'Deployed revision is invalid'
PHOTOLAMP_DEPLOY_SHA="$deployed_sha" bash infra/scripts/backup-database.sh

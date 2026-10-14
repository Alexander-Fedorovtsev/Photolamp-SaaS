#!/usr/bin/env bash
set -Eeuo pipefail

fail() { printf '[photolamp-backup-storage] %s\n' "$*" >&2; exit 1; }
readonly storage_root=/mnt/raid/photolamp-test
readonly backup_dir="$storage_root/backups"
[[ -d "$storage_root" && ! -L "$storage_root" ]] || fail 'Photolamp storage root is missing or unsafe'
[[ "$(stat -c '%u:%g:%a' "$storage_root")" == '0:0:755' ]] || fail 'Photolamp storage root must remain root-owned with mode 755'
[[ ! -e "$backup_dir" || -d "$backup_dir" ]] || fail 'Backup path must be a directory'
[[ ! -L "$backup_dir" ]] || fail 'Refusing a symlink as backup storage'

docker run --rm --user 0:0 --mount "type=bind,src=$storage_root,dst=/storage" \
  postgres:16-bookworm install -d -o "$(id -u)" -g "$(id -g)" -m 0700 /storage/backups
[[ "$(stat -c '%u:%g:%a' "$backup_dir")" == "$(id -u):$(id -g):700" ]] || fail 'Cannot secure backup storage'
printf '[photolamp-backup-storage] Encrypted backup directory ready\n'

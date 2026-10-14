#!/usr/bin/env bash
set -Eeuo pipefail

fail() { printf '[photolamp-bootstrap] %s\n' "$*" >&2; exit 1; }
[[ "$EUID" -eq 0 ]] || fail 'Run bootstrap as root'
[[ $# -eq 1 && "$1" =~ ^[a-z_][a-z0-9_-]*$ ]] || fail 'Usage: bootstrap-host.sh DEPLOY_USER'
id "$1" >/dev/null 2>&1 || fail 'Deploy user must already exist'
mountpoint -q /mnt/raid || fail '/mnt/raid must already be mounted'

for path in /opt/photolamp-test /mnt/raid/photolamp-test /mnt/raid/photolamp-test/postgres /mnt/raid/photolamp-test/backups; do
  [[ ! -L "$path" ]] || fail 'Refusing a symlink in staging paths'
  [[ ! -e "$path" || -d "$path" ]] || fail 'Staging paths must be directories'
done
# Debian postgres:16-bookworm runs as 999:999, unlike SelfOS's Alpine image.
install -d -o "$(id -u "$1")" -g "$(id -g "$1")" -m 0750 /opt/photolamp-test
install -d -o 0 -g 0 -m 0755 /mnt/raid/photolamp-test
install -d -o 999 -g 999 -m 0700 /mnt/raid/photolamp-test/postgres
install -d -o "$(id -u "$1")" -g "$(id -g "$1")" -m 0700 /mnt/raid/photolamp-test/backups
printf '[photolamp-bootstrap] Prepared only Photolamp directories\n'

#!/usr/bin/env bash
set -Eeuo pipefail

deploy_sha="${1:-}"
ssh_key="${2:-}"
known_hosts="${3:-}"
host="${4:-}"
user="${5:-}"
port="${6:-}"
remote_script="${7:-}"
max_attempts=4
retry_delay_seconds=10

[[ "$deploy_sha" =~ ^[a-f0-9]{40}$ ]] || {
  echo "Expected one exact Git SHA" >&2
  exit 1
}
[[ -f "$ssh_key" && -f "$known_hosts" && -f "$remote_script" ]] || {
  echo "SSH deployment files are unavailable" >&2
  exit 1
}

# The loader expands only on the remote host after the script is fully spooled.
# shellcheck disable=SC2016
remote_loader='temporary=$(mktemp); trap "rm -f -- \"$temporary\"" EXIT HUP INT TERM; cat > "$temporary"; chmod 700 "$temporary"; bash "$temporary" "$1"'
remote_command="bash -ceu '$remote_loader' bash '$deploy_sha'"

ssh_command=(
  ssh -i "$ssh_key" -p "$port"
  -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes
  -o ConnectTimeout=15 -o ConnectionAttempts=3
  -o ServerAliveInterval=30 -o ServerAliveCountMax=4
  -o "UserKnownHostsFile=$known_hosts"
  "$user@$host" "$remote_command"
)

for ((attempt = 1; attempt <= max_attempts; attempt++)); do
  if "${ssh_command[@]}" < "$remote_script"; then
    exit 0
  else
    status=$?
  fi
  if ((status != 255)); then
    exit "$status"
  fi
  if ((attempt < max_attempts)); then
    echo "SSH transport unavailable; retrying ($((attempt + 1))/$max_attempts)" >&2
    sleep "$retry_delay_seconds"
  fi
done

echo "SSH transport failed after $max_attempts attempts" >&2
exit 255

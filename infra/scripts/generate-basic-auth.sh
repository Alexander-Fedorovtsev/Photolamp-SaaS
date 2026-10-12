#!/usr/bin/env bash
set -Eeuo pipefail
command -v htpasswd >/dev/null || { echo 'Install apache2-utils to obtain htpasswd' >&2; exit 1; }
read -r -p 'Basic Auth username: ' auth_user
[[ "$auth_user" =~ ^[a-zA-Z0-9_.-]+$ ]] || { echo 'Invalid username' >&2; exit 1; }
read -r -s -p 'Password (at least 16 characters): ' auth_password
printf '\n' >&2
read -r -s -p 'Repeat password: ' repeated_password
printf '\n' >&2
[[ ${#auth_password} -ge 16 && "$auth_password" == "$repeated_password" ]] || {
  echo 'Passwords must match and have at least 16 characters' >&2; exit 1;
}
auth_hash="$(printf '%s\n' "$auth_password" | htpasswd -nBi "$auth_user")"
unset auth_password repeated_password
printf "TRAEFIK_BASIC_AUTH_USERS='%s'\n" "$auth_hash"

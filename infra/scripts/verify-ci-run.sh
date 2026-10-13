#!/usr/bin/env bash
set -Eeuo pipefail

deploy_sha="${1:-}"
repository="${2:-}"
output_file="${3:-}"
github_api_url="${GITHUB_API_URL:-https://api.github.com}"
max_attempts=6
retry_delay_seconds=5

[[ "$deploy_sha" =~ ^[a-f0-9]{40}$ ]] || {
  echo "Expected one exact Git SHA" >&2
  exit 1
}
[[ "$repository" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || {
  echo "Expected owner/repository" >&2
  exit 1
}
[[ -n "$output_file" && -n "${GH_TOKEN:-}" ]] || {
  echo "CI verification configuration is incomplete" >&2
  exit 1
}

for ((attempt = 1; attempt <= max_attempts; attempt++)); do
  if curl --fail --silent --show-error \
    --header "Accept: application/vnd.github+json" \
    --header "Authorization: Bearer $GH_TOKEN" \
    --header "X-GitHub-Api-Version: 2022-11-28" \
    "$github_api_url/repos/$repository/actions/workflows/ci.yml/runs?branch=main&event=push&status=completed&head_sha=$deploy_sha&per_page=10" \
    > "$output_file" && \
    python3 infra/scripts/check-ci-run.py "$deploy_sha" "$repository" \
      < "$output_file" 2>/dev/null; then
    exit 0
  fi
  if ((attempt < max_attempts)); then
    echo "Exact CI run is not visible yet; retrying ($((attempt + 1))/$max_attempts)" >&2
    sleep "$retry_delay_seconds"
  fi
done

echo "Exact main revision must have a successful CI push run after $max_attempts attempts" >&2
exit 1

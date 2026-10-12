"""Require the most recent completed push CI for this exact repository/main revision."""

import json
import re
import sys


def approved(payload: dict, sha: str, repository: str) -> bool:
    if not re.fullmatch(r"[a-f0-9]{40}", sha):
        return False
    matching = [
        run
        for run in payload.get("workflow_runs", [])
        if run.get("head_sha") == sha
        and run.get("head_branch") == "main"
        and run.get("event") == "push"
        and (run.get("head_repository") or {}).get("full_name") == repository
    ]
    return (
        bool(matching)
        and max(
            matching,
            key=lambda run: (
                run.get("run_number", 0),
                run.get("run_attempt", 1),
                run["id"],
            ),
        ).get("conclusion")
        == "success"
    )


if __name__ == "__main__":
    if len(sys.argv) != 3 or not approved(
        json.load(sys.stdin), sys.argv[1], sys.argv[2]
    ):
        raise SystemExit("Exact main revision must have a successful CI push run")

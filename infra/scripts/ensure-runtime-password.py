"""Provision a separate runtime database password without printing it."""

import os
import re
import secrets
import stat
import sys
import tempfile
from pathlib import Path

KEY = "POSTGRES_RUNTIME_PASSWORD"
PLACEHOLDERS = {
    "GENERATED_AUTOMATICALLY_ON_FIRST_DEPLOY",
    "REPLACE_WITH_RANDOM_HEX",
}


def ensure(path: Path) -> bool:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError("Server .env is missing or unsafe")
    content = path.read_text()
    match = re.search(rf"^{KEY}=(.*)$", content, re.MULTILINE)
    if match and match.group(1).strip("'\"") not in PLACEHOLDERS:
        if not re.fullmatch(r"[a-fA-F0-9]{64}", match.group(1).strip("'\"")):
            raise ValueError(
                "Runtime database password must be 64 hexadecimal characters"
            )
        return False
    line = f"{KEY}={secrets.token_hex(32)}"
    if match:
        content = content[: match.start()] + line + content[match.end() :]
    else:
        content = content.rstrip("\n") + "\n" + line + "\n"
    descriptor, temporary_name = tempfile.mkstemp(prefix=".env.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chown(temporary, info.st_uid, info.st_gid)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


if __name__ == "__main__":
    try:
        created = ensure(Path(sys.argv[1]))
    except (OSError, ValueError, IndexError) as error:
        raise SystemExit(
            f"Cannot provision runtime database credential: {error}"
        ) from None
    print(
        "Runtime database credential provisioned"
        if created
        else "Runtime database credential ready"
    )

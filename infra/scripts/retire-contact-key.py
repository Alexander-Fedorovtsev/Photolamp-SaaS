"""Remove one inactive contact key after the database reference check."""

import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def retire(path: Path, version: int) -> None:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError("Server .env is missing or unsafe")
    content = path.read_text()
    active_match = re.search(
        r"^CONTACT_ACTIVE_KEY_VERSION=(.*)$", content, re.MULTILINE
    )
    keyring_match = re.search(r"^CONTACT_ENCRYPTION_KEYS=(.*)$", content, re.MULTILINE)
    if not active_match or not keyring_match:
        raise ValueError("Contact keyring configuration is incomplete")
    try:
        active = int(_unquote(active_match.group(1)))
        keyring = json.loads(_unquote(keyring_match.group(1)))
    except (ValueError, json.JSONDecodeError) as error:
        raise ValueError("Contact keyring configuration is invalid") from error
    if version == active:
        raise ValueError("The active contact key cannot be retired")
    if not isinstance(keyring, dict) or str(version) not in keyring:
        raise ValueError("The retiring contact key is absent from the keyring")
    del keyring[str(version)]
    serialized = json.dumps(keyring, separators=(",", ":"), sort_keys=True)
    content = (
        content[: keyring_match.start()]
        + f"CONTACT_ENCRYPTION_KEYS='{serialized}'"
        + content[keyring_match.end() :]
    )
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


if __name__ == "__main__":
    try:
        if len(sys.argv) != 3 or not sys.argv[2].isdecimal():
            raise ValueError("Usage: retire-contact-key.py ENV_FILE VERSION")
        retire(Path(sys.argv[1]), int(sys.argv[2]))
    except (OSError, ValueError) as error:
        raise SystemExit(f"Cannot retire contact key: {error}") from None
    print("Unused contact key retired")

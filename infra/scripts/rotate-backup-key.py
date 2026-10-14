"""Add a new active backup key while retaining keys for existing archives."""

import json
import os
import re
import secrets
import stat
import sys
import tempfile
from pathlib import Path

KEY_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def rotate(path: Path, new_key_id: str) -> None:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError("Server .env is missing or unsafe")
    if not KEY_ID_PATTERN.fullmatch(new_key_id):
        raise ValueError("New backup key ID is invalid")
    content = path.read_text()
    active = re.search(r"^BACKUP_ACTIVE_KEY_ID=(.*)$", content, re.MULTILINE)
    keyring_match = re.search(r"^BACKUP_ENCRYPTION_KEYS=(.*)$", content, re.MULTILINE)
    if not active or not keyring_match:
        raise ValueError("Provision the backup keyring before rotation")
    serialized = keyring_match.group(1).strip()
    if (
        len(serialized) >= 2
        and serialized[0] == serialized[-1]
        and serialized[0] in "'\""
    ):
        serialized = serialized[1:-1]
    try:
        keyring = json.loads(serialized)
    except json.JSONDecodeError as error:
        raise ValueError("Backup keyring must be valid JSON") from error
    if not isinstance(keyring, dict) or not keyring:
        raise ValueError("Backup keyring must contain existing keys")
    if not all(
        isinstance(key_id, str)
        and KEY_ID_PATTERN.fullmatch(key_id)
        and isinstance(key, str)
        and re.fullmatch(r"[a-fA-F0-9]{64}", key)
        for key_id, key in keyring.items()
    ):
        raise ValueError("Backup keyring contains an invalid entry")
    if new_key_id in keyring:
        raise ValueError("Backup key ID already exists")
    if len(keyring) >= 8:
        raise ValueError("Remove expired backup keys before adding another")
    keyring[new_key_id] = secrets.token_hex(32)
    serialized = json.dumps(keyring, separators=(",", ":"), sort_keys=True)
    content = (
        content[: active.start()]
        + f"BACKUP_ACTIVE_KEY_ID={new_key_id}"
        + content[active.end() :]
    )
    keyring_match = re.search(r"^BACKUP_ENCRYPTION_KEYS=(.*)$", content, re.MULTILINE)
    if keyring_match is None:
        raise ValueError("Backup keyring disappeared during rotation")
    content = (
        content[: keyring_match.start()]
        + f"BACKUP_ENCRYPTION_KEYS='{serialized}'"
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
        if len(sys.argv) != 3:
            raise ValueError("Usage: rotate-backup-key.py ENV_FILE NEW_KEY_ID")
        rotate(Path(sys.argv[1]), sys.argv[2])
    except (OSError, ValueError) as error:
        raise SystemExit(f"Cannot rotate backup key: {error}") from None
    print("New backup key generated and activated; previous keys retained")

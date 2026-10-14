"""Provision and validate a versioned backup keyring without printing secrets."""

import json
import os
import re
import secrets
import stat
import sys
import tempfile
from pathlib import Path

ACTIVE_KEY = "BACKUP_ACTIVE_KEY_ID"
KEYRING = "BACKUP_ENCRYPTION_KEYS"
DEFAULT_KEY_ID = "v1"
PLACEHOLDER = "GENERATED_AUTOMATICALLY_ON_FIRST_DEPLOY"
KEY_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def _value(content: str, name: str) -> str | None:
    match = re.search(rf"^{name}=(.*)$", content, re.MULTILINE)
    if not match:
        return None
    value = match.group(1).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1]
    return value


def _validate(active_key: str, serialized_keyring: str) -> None:
    if not KEY_ID_PATTERN.fullmatch(active_key):
        raise ValueError("Backup active key ID is invalid")
    try:
        keyring = json.loads(serialized_keyring)
    except json.JSONDecodeError as error:
        raise ValueError("Backup keyring must be valid JSON") from error
    if not isinstance(keyring, dict) or active_key not in keyring:
        raise ValueError("Backup keyring must contain the active key")
    for key_id, key in keyring.items():
        if (
            not isinstance(key_id, str)
            or not KEY_ID_PATTERN.fullmatch(key_id)
            or not isinstance(key, str)
            or not re.fullmatch(r"[a-fA-F0-9]{64}", key)
        ):
            raise ValueError("Backup keyring contains an invalid entry")


def ensure(path: Path) -> bool:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError("Server .env is missing or unsafe")
    content = path.read_text()
    active_key = _value(content, ACTIVE_KEY)
    serialized_keyring = _value(content, KEYRING)
    if active_key == PLACEHOLDER and serialized_keyring == PLACEHOLDER:
        content = re.sub(rf"^{ACTIVE_KEY}=.*\n?", "", content, flags=re.MULTILINE)
        content = re.sub(rf"^{KEYRING}=.*\n?", "", content, flags=re.MULTILINE)
        active_key = None
        serialized_keyring = None
    if active_key is not None or serialized_keyring is not None:
        if active_key is None or serialized_keyring is None:
            raise ValueError("Backup keyring configuration is incomplete")
        _validate(active_key, serialized_keyring)
        return False

    active_key = DEFAULT_KEY_ID
    serialized_keyring = json.dumps(
        {active_key: secrets.token_hex(32)}, separators=(",", ":")
    )
    addition = f"{ACTIVE_KEY}={active_key}\n{KEYRING}='{serialized_keyring}'\n"
    descriptor, temporary_name = tempfile.mkstemp(prefix=".env.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(content.rstrip("\n") + "\n" + addition)
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
        raise SystemExit(f"Cannot provision backup keyring: {error}") from None
    print("Backup keyring provisioned" if created else "Backup keyring ready")

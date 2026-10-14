"""Add and activate a new contact key while retaining existing versions."""

import base64
import json
import os
import re
import secrets
import stat
import sys
import tempfile
from pathlib import Path


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def rotate(path: Path, new_version: int) -> None:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError("Server .env is missing or unsafe")
    if not 1 <= new_version <= 32767:
        raise ValueError("New contact key version must be between 1 and 32767")
    content = path.read_text()
    active_match = re.search(
        r"^CONTACT_ACTIVE_KEY_VERSION=(.*)$", content, re.MULTILINE
    )
    keyring_match = re.search(r"^CONTACT_ENCRYPTION_KEYS=(.*)$", content, re.MULTILINE)
    if not active_match or not keyring_match:
        raise ValueError("Provision the contact keyring before rotation")
    try:
        active = int(_unquote(active_match.group(1)))
        keyring = json.loads(_unquote(keyring_match.group(1)))
    except (ValueError, json.JSONDecodeError) as error:
        raise ValueError("Contact keyring configuration is invalid") from error
    if (
        not isinstance(keyring, dict)
        or str(active) not in keyring
        or len(keyring) >= 8
        or any(not isinstance(key, str) for key in keyring.values())
    ):
        raise ValueError("Contact keyring is invalid or full")
    versions = {int(version) for version in keyring if version.isdecimal()}
    if len(versions) != len(keyring) or new_version <= max(versions):
        raise ValueError(
            "New contact key version must be greater than every existing version"
        )
    keyring[str(new_version)] = base64.urlsafe_b64encode(
        secrets.token_bytes(32)
    ).decode("ascii")
    serialized = json.dumps(keyring, separators=(",", ":"), sort_keys=True)
    content = (
        content[: active_match.start()]
        + f"CONTACT_ACTIVE_KEY_VERSION={new_version}"
        + content[active_match.end() :]
    )
    keyring_match = re.search(r"^CONTACT_ENCRYPTION_KEYS=(.*)$", content, re.MULTILINE)
    if keyring_match is None:
        raise ValueError("Contact keyring disappeared during rotation")
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
            raise ValueError("Usage: rotate-contact-key.py ENV_FILE NEW_VERSION")
        rotate(Path(sys.argv[1]), int(sys.argv[2]))
    except (OSError, ValueError) as error:
        raise SystemExit(f"Cannot rotate contact key: {error}") from None
    print("New contact key generated and activated; previous keys retained")

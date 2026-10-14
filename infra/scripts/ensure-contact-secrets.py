"""Provision or migrate the versioned contact keyring without printing secrets."""

import base64
import binascii
import json
import os
import re
import secrets
import stat
import sys
import tempfile
from pathlib import Path

ACTIVE_KEY = "CONTACT_ACTIVE_KEY_VERSION"
KEYRING = "CONTACT_ENCRYPTION_KEYS"
LEGACY_KEY = "CONTACT_ENCRYPTION_KEY"
PLACEHOLDER = "GENERATED_AUTOMATICALLY_ON_FIRST_DEPLOY"
LOCAL_SYNTHETIC_KEY = "cGhvdG9sYW1wLWxvY2FsLXN5bnRoZXRpYy1rZXktdjE="


def _value(content: str, name: str) -> str | None:
    match = re.search(rf"^{name}=(.*)$", content, re.MULTILINE)
    if not match:
        return None
    value = match.group(1).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1]
    return value


def _decode(encoded: str) -> bytes:
    try:
        return base64.b64decode(encoded.encode("ascii"), altchars=b"-_", validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError) as error:
        raise ValueError("Contact key must be URL-safe base64") from error


def _validate(active: str, serialized: str) -> None:
    if (
        not active.isdecimal()
        or active != str(int(active))
        or not 1 <= int(active) <= 32767
    ):
        raise ValueError("Contact active key version is invalid")
    try:
        keyring = json.loads(serialized)
    except json.JSONDecodeError as error:
        raise ValueError("Contact keyring must be valid JSON") from error
    if (
        not isinstance(keyring, dict)
        or not 1 <= len(keyring) <= 8
        or active not in keyring
    ):
        raise ValueError("Contact keyring must contain the active key")
    decoded: set[bytes] = set()
    for version, key in keyring.items():
        if (
            not isinstance(version, str)
            or not version.isdecimal()
            or version != str(int(version))
            or not 1 <= int(version) <= 32767
            or not isinstance(key, str)
        ):
            raise ValueError("Contact keyring contains an invalid entry")
        raw = _decode(key)
        if len(raw) != 32 or raw in decoded:
            raise ValueError(
                "Contact keyring contains invalid or repeated key material"
            )
        decoded.add(raw)


def _random_key() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")


def _replace_atomically(path: Path, content: str, info: os.stat_result) -> None:
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


def ensure(path: Path) -> bool:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError("Server .env is missing or unsafe")
    content = path.read_text()
    active = _value(content, ACTIVE_KEY)
    serialized = _value(content, KEYRING)
    legacy = _value(content, LEGACY_KEY)
    placeholders = active == PLACEHOLDER and serialized == PLACEHOLDER
    if (active is None) != (serialized is None):
        raise ValueError("Contact keyring configuration is incomplete")
    if active is not None and not placeholders:
        _validate(active, serialized or "")
        if legacy is not None:
            raise ValueError("Remove the obsolete CONTACT_ENCRYPTION_KEY setting")
        return False

    content = re.sub(rf"^{ACTIVE_KEY}=.*\n?", "", content, flags=re.MULTILINE)
    content = re.sub(rf"^{KEYRING}=.*\n?", "", content, flags=re.MULTILINE)
    content = re.sub(rf"^{LEGACY_KEY}=.*\n?", "", content, flags=re.MULTILINE)
    if placeholders or legacy == "GENERATE_BEFORE_REAL_CONTACTS":
        key = _random_key()
    elif legacy is not None:
        key = legacy
    else:
        # Existing staging installs historically used this implicit key. Keeping it
        # as v1 preserves their synthetic rows until an explicit rotation.
        key = LOCAL_SYNTHETIC_KEY
    _validate("1", json.dumps({"1": key}))
    serialized = json.dumps({"1": key}, separators=(",", ":"))
    addition = f"{ACTIVE_KEY}=1\n{KEYRING}='{serialized}'\n"
    _replace_atomically(path, content.rstrip("\n") + "\n" + addition, info)
    return True


if __name__ == "__main__":
    try:
        created = ensure(Path(sys.argv[1]))
    except (OSError, ValueError, IndexError) as error:
        raise SystemExit(f"Cannot provision contact keyring: {error}") from None
    print("Contact keyring provisioned" if created else "Contact keyring ready")

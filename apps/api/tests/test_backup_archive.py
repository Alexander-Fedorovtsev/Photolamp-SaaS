import io
import json
import secrets

import pytest

from app.backup_archive import (
    BackupArchiveError,
    archive_key_id,
    decrypt_stream,
    encrypt_stream,
    parse_keyring,
)


def test_backup_archive_round_trip_selects_versioned_key() -> None:
    keys = {"v1": secrets.token_bytes(32), "v2": secrets.token_bytes(32)}
    plaintext = (b"synthetic pg dump\x00" * 100_000) + b"end"
    encrypted = io.BytesIO()

    encrypt_stream(io.BytesIO(plaintext), encrypted, "v2", keys["v2"])

    archive = encrypted.getvalue()
    assert plaintext[:64] not in archive
    assert archive_key_id(io.BytesIO(archive)) == "v2"
    restored = io.BytesIO()
    assert decrypt_stream(io.BytesIO(archive), restored, keys) == "v2"
    assert restored.getvalue() == plaintext


def test_backup_archive_rejects_tampering_without_plaintext_output() -> None:
    key = secrets.token_bytes(32)
    encrypted = io.BytesIO()
    encrypt_stream(io.BytesIO(b"private backup payload"), encrypted, "v1", key)
    archive = bytearray(encrypted.getvalue())
    archive[-17] ^= 1
    destination = io.BytesIO()

    with pytest.raises(BackupArchiveError, match="authentication failed"):
        decrypt_stream(io.BytesIO(archive), destination, {"v1": key})

    assert destination.getvalue() == b""


def test_backup_archive_requires_available_well_formed_keys() -> None:
    key = secrets.token_bytes(32)
    serialized = json.dumps({"v1": key.hex()})
    assert parse_keyring(serialized) == {"v1": key}
    for invalid in ("", "[]", '{"Bad ID":"00"}', '{"v1":"short"}'):
        with pytest.raises(BackupArchiveError):
            parse_keyring(invalid)

    encrypted = io.BytesIO()
    encrypt_stream(io.BytesIO(b"payload"), encrypted, "v1", key)
    with pytest.raises(BackupArchiveError, match="unavailable"):
        decrypt_stream(io.BytesIO(encrypted.getvalue()), io.BytesIO(), {"v2": key})

"""Versioned authenticated encryption for database backup streams."""

import json
import os
import re
import shutil
import struct
import sys
import tempfile
from collections.abc import Mapping
from typing import BinaryIO

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

MAGIC = b"PHOTOLAMP-BACKUP\x01"
NONCE_SIZE = 12
TAG_SIZE = 16
CHUNK_SIZE = 1024 * 1024
KEY_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


class BackupArchiveError(ValueError):
    """Raised when an archive or its key configuration is invalid."""


def parse_keyring(serialized: str) -> dict[str, bytes]:
    try:
        value = json.loads(serialized)
    except json.JSONDecodeError as error:
        raise BackupArchiveError("Backup keyring must be valid JSON") from error
    if not isinstance(value, dict) or not value:
        raise BackupArchiveError("Backup keyring must contain at least one key")
    keyring: dict[str, bytes] = {}
    for key_id, encoded_key in value.items():
        if not isinstance(key_id, str) or not KEY_ID_PATTERN.fullmatch(key_id):
            raise BackupArchiveError("Backup key IDs must use lowercase safe characters")
        if not isinstance(encoded_key, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", encoded_key):
            raise BackupArchiveError("Each backup key must contain 32 hexadecimal bytes")
        keyring[key_id] = bytes.fromhex(encoded_key)
    return keyring


def _read_exact(source: BinaryIO, size: int) -> bytes:
    value = source.read(size)
    if len(value) != size:
        raise BackupArchiveError("Backup archive is truncated")
    return value


def encrypt_stream(source: BinaryIO, destination: BinaryIO, key_id: str, key: bytes) -> None:
    if not KEY_ID_PATTERN.fullmatch(key_id) or len(key) != 32:
        raise BackupArchiveError("Invalid active backup key")
    encoded_key_id = key_id.encode("ascii")
    nonce = os.urandom(NONCE_SIZE)
    header = MAGIC + struct.pack("B", len(encoded_key_id)) + encoded_key_id + nonce
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(header)
    destination.write(header)
    while chunk := source.read(CHUNK_SIZE):
        destination.write(encryptor.update(chunk))
    destination.write(encryptor.finalize())
    destination.write(encryptor.tag)


def decrypt_stream(source: BinaryIO, destination: BinaryIO, keyring: Mapping[str, bytes]) -> str:
    prefix = _read_exact(source, len(MAGIC))
    if prefix != MAGIC:
        raise BackupArchiveError("Unsupported backup archive format")
    key_id_size = struct.unpack("B", _read_exact(source, 1))[0]
    if key_id_size == 0 or key_id_size > 32:
        raise BackupArchiveError("Invalid backup key ID")
    try:
        key_id = _read_exact(source, key_id_size).decode("ascii")
    except UnicodeDecodeError as error:
        raise BackupArchiveError("Invalid backup key ID") from error
    if not KEY_ID_PATTERN.fullmatch(key_id) or key_id not in keyring:
        raise BackupArchiveError("Backup archive key is unavailable")
    nonce = _read_exact(source, NONCE_SIZE)
    header = prefix + struct.pack("B", key_id_size) + key_id.encode("ascii") + nonce
    decryptor = Cipher(algorithms.AES(keyring[key_id]), modes.GCM(nonce)).decryptor()
    decryptor.authenticate_additional_data(header)
    tail = b""
    # Do not release unauthenticated plaintext. The pilot data set is small and
    # the maintenance container places this temporary file on tmpfs.
    with tempfile.TemporaryFile() as authenticated_plaintext:
        while chunk := source.read(CHUNK_SIZE):
            combined = tail + chunk
            if len(combined) <= TAG_SIZE:
                tail = combined
                continue
            ciphertext, tail = combined[:-TAG_SIZE], combined[-TAG_SIZE:]
            authenticated_plaintext.write(decryptor.update(ciphertext))
        if len(tail) != TAG_SIZE:
            raise BackupArchiveError("Backup archive is truncated")
        try:
            authenticated_plaintext.write(decryptor.finalize_with_tag(tail))
        except InvalidTag as error:
            raise BackupArchiveError("Backup archive authentication failed") from error
        authenticated_plaintext.seek(0)
        shutil.copyfileobj(authenticated_plaintext, destination, CHUNK_SIZE)
    return key_id


def archive_key_id(source: BinaryIO) -> str:
    if _read_exact(source, len(MAGIC)) != MAGIC:
        raise BackupArchiveError("Unsupported backup archive format")
    key_id_size = struct.unpack("B", _read_exact(source, 1))[0]
    if key_id_size == 0 or key_id_size > 32:
        raise BackupArchiveError("Invalid backup key ID")
    try:
        key_id = _read_exact(source, key_id_size).decode("ascii")
    except UnicodeDecodeError as error:
        raise BackupArchiveError("Invalid backup key ID") from error
    if not KEY_ID_PATTERN.fullmatch(key_id):
        raise BackupArchiveError("Invalid backup key ID")
    return key_id


def main() -> None:
    try:
        command = sys.argv[1]
        keyring = parse_keyring(os.environ.get("BACKUP_ENCRYPTION_KEYS", ""))
        if command == "encrypt":
            key_id = os.environ.get("BACKUP_ACTIVE_KEY_ID", "")
            if key_id not in keyring:
                raise BackupArchiveError("Active backup key is unavailable")
            encrypt_stream(sys.stdin.buffer, sys.stdout.buffer, key_id, keyring[key_id])
        elif command == "decrypt":
            decrypt_stream(sys.stdin.buffer, sys.stdout.buffer, keyring)
        elif command == "inspect":
            print(archive_key_id(sys.stdin.buffer))
        else:
            raise BackupArchiveError("Expected encrypt, decrypt or inspect command")
    except (BackupArchiveError, OSError, IndexError) as error:
        raise SystemExit(f"Backup archive error: {error}") from None


if __name__ == "__main__":
    main()

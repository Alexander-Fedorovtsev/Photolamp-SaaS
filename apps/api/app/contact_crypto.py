"""Authenticated encryption for tenant-scoped contact records."""

import base64
import binascii
import json
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAX_CONTACT_KEYS = 8
MAX_KEY_VERSION = 32767


class UnknownContactKeyVersion(ValueError):
    """The row references a key that is unavailable to this process."""


@dataclass(frozen=True, slots=True)
class EncryptedContact:
    nonce: bytes
    ciphertext: bytes
    key_version: int = 1


def _decode_key(encoded_key: str) -> bytes:
    try:
        key = base64.b64decode(encoded_key.encode("ascii"), altchars=b"-_", validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError) as error:
        raise ValueError("Contact encryption key must be URL-safe base64") from error
    if len(key) != 32:
        raise ValueError("Contact encryption key must contain 32 bytes")
    return key


def parse_contact_keyring(serialized: str) -> dict[int, str]:
    """Parse a bounded JSON keyring with canonical positive integer versions."""

    try:
        raw = json.loads(serialized)
    except json.JSONDecodeError as error:
        raise ValueError("Contact encryption keyring must be valid JSON") from error
    if not isinstance(raw, dict) or not raw or len(raw) > MAX_CONTACT_KEYS:
        raise ValueError(f"Contact encryption keyring must contain 1-{MAX_CONTACT_KEYS} keys")
    keyring: dict[int, str] = {}
    decoded_keys: set[bytes] = set()
    for raw_version, encoded_key in raw.items():
        if (
            not isinstance(raw_version, str)
            or not raw_version.isascii()
            or not raw_version.isdecimal()
            or raw_version != str(int(raw_version))
            or not 1 <= int(raw_version) <= MAX_KEY_VERSION
            or not isinstance(encoded_key, str)
        ):
            raise ValueError("Contact encryption keyring contains an invalid version")
        decoded = _decode_key(encoded_key)
        if decoded in decoded_keys:
            raise ValueError("Contact encryption keyring must not reuse key material")
        decoded_keys.add(decoded)
        keyring[int(raw_version)] = encoded_key
    return keyring


class ContactCipher:
    """Encrypt contact JSON with record identity bound as authenticated data."""

    def __init__(
        self,
        encoded_keys: str | Mapping[int, str],
        active_key_version: int = 1,
    ) -> None:
        keys = {1: encoded_keys} if isinstance(encoded_keys, str) else dict(encoded_keys)
        if not 1 <= len(keys) <= MAX_CONTACT_KEYS or not all(
            isinstance(version, int) and 1 <= version <= MAX_KEY_VERSION for version in keys
        ):
            raise ValueError("Contact encryption key versions are invalid")
        if active_key_version not in keys:
            raise ValueError("Active contact encryption key is unavailable")
        decoded = {version: _decode_key(key) for version, key in keys.items()}
        if len(set(decoded.values())) != len(decoded):
            raise ValueError("Contact encryption keys must not reuse key material")
        self._ciphers = {version: AESGCM(key) for version, key in decoded.items()}
        self.active_key_version = active_key_version

    @property
    def available_key_versions(self) -> frozenset[int]:
        return frozenset(self._ciphers)

    @staticmethod
    def _associated_data(
        tenant_id: UUID,
        record_type: str,
        record_id: UUID,
    ) -> bytes:
        return f"photolamp:v1:{record_type}:{tenant_id}:{record_id}".encode()

    def encrypt(
        self,
        tenant_id: UUID,
        record_type: str,
        record_id: UUID,
        payload: Mapping[str, str | None],
    ) -> EncryptedContact:
        nonce = secrets.token_bytes(12)
        plaintext = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        version = self.active_key_version
        ciphertext = self._ciphers[version].encrypt(
            nonce,
            plaintext,
            self._associated_data(tenant_id, record_type, record_id),
        )
        return EncryptedContact(nonce=nonce, ciphertext=ciphertext, key_version=version)

    def decrypt(
        self,
        tenant_id: UUID,
        record_type: str,
        record_id: UUID,
        encrypted: EncryptedContact,
    ) -> dict[str, str | None]:
        cipher = self._ciphers.get(encrypted.key_version)
        if cipher is None:
            raise UnknownContactKeyVersion(
                f"Contact key version {encrypted.key_version} is unavailable"
            )
        plaintext = cipher.decrypt(
            encrypted.nonce,
            encrypted.ciphertext,
            self._associated_data(tenant_id, record_type, record_id),
        )
        value = json.loads(plaintext)
        if not isinstance(value, dict) or not all(
            isinstance(key, str) and (item is None or isinstance(item, str))
            for key, item in value.items()
        ):
            raise ValueError("Encrypted contact payload must be a string object")
        return value

"""Authenticated encryption for tenant-scoped contact records."""

import base64
import json
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


@dataclass(frozen=True, slots=True)
class EncryptedContact:
    nonce: bytes
    ciphertext: bytes
    key_version: int = 1


class ContactCipher:
    """Encrypt contact JSON with record identity bound as authenticated data."""

    def __init__(self, encoded_key: str) -> None:
        key = base64.urlsafe_b64decode(encoded_key.encode())
        if len(key) != 32:
            raise ValueError("Contact encryption key must contain 32 bytes")
        self._cipher = AESGCM(key)

    @staticmethod
    def _associated_data(tenant_id: UUID, record_type: str, record_id: UUID) -> bytes:
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
        ciphertext = self._cipher.encrypt(
            nonce, plaintext, self._associated_data(tenant_id, record_type, record_id)
        )
        return EncryptedContact(nonce=nonce, ciphertext=ciphertext)

    def decrypt(
        self,
        tenant_id: UUID,
        record_type: str,
        record_id: UUID,
        encrypted: EncryptedContact,
    ) -> dict[str, str | None]:
        plaintext = self._cipher.decrypt(
            encrypted.nonce,
            encrypted.ciphertext,
            self._associated_data(tenant_id, record_type, record_id),
        )
        value = json.loads(plaintext)
        if not isinstance(value, dict):
            raise ValueError("Encrypted contact payload must be an object")
        return value

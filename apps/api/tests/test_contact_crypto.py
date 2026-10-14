import base64
import secrets
from uuid import uuid4

import pytest
from cryptography.exceptions import InvalidTag

from app.contact_crypto import (
    ContactCipher,
    EncryptedContact,
    UnknownContactKeyVersion,
    parse_contact_keyring,
)


def test_contact_cipher_round_trip_binds_tenant_and_record() -> None:
    cipher = ContactCipher(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
    tenant_id = uuid4()
    record_id = uuid4()
    payload = {"phone_e164": "+70000000000", "recipient_name": "Тестовый клиент"}

    encrypted = cipher.encrypt(tenant_id, "customer", record_id, payload)

    assert b"+70000000000" not in encrypted.ciphertext
    assert cipher.decrypt(tenant_id, "customer", record_id, encrypted) == payload
    with pytest.raises(InvalidTag):
        cipher.decrypt(uuid4(), "customer", record_id, encrypted)
    with pytest.raises(InvalidTag):
        cipher.decrypt(tenant_id, "order", record_id, encrypted)


def test_contact_cipher_reads_old_version_and_writes_active_version() -> None:
    old_key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    new_key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    tenant_id = uuid4()
    record_id = uuid4()
    payload = {"phone_e164": "+70000000000"}
    old_cipher = ContactCipher({1: old_key}, active_key_version=1)
    encrypted_v1 = old_cipher.encrypt(tenant_id, "customer", record_id, payload)
    rotating_cipher = ContactCipher({1: old_key, 2: new_key}, active_key_version=2)

    assert rotating_cipher.decrypt(tenant_id, "customer", record_id, encrypted_v1) == payload
    encrypted_v2 = rotating_cipher.encrypt(tenant_id, "customer", record_id, payload)
    assert encrypted_v2.key_version == 2
    assert rotating_cipher.decrypt(tenant_id, "customer", record_id, encrypted_v2) == payload

    with pytest.raises(UnknownContactKeyVersion):
        old_cipher.decrypt(
            tenant_id,
            "customer",
            record_id,
            EncryptedContact(encrypted_v2.nonce, encrypted_v2.ciphertext, 2),
        )


def test_contact_keyring_rejects_ambiguous_or_invalid_entries() -> None:
    key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    assert parse_contact_keyring(f'{{"1":"{key}"}}') == {1: key}

    for serialized in (
        "{}",
        f'{{"01":"{key}"}}',
        f'{{"1":"{key}","2":"{key}"}}',
        '{"1":"not-base64"}',
    ):
        with pytest.raises(ValueError):
            parse_contact_keyring(serialized)

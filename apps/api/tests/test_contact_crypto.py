import base64
import secrets
from uuid import uuid4

import pytest
from cryptography.exceptions import InvalidTag

from app.contact_crypto import ContactCipher


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

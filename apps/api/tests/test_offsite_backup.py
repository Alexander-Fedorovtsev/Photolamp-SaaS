import base64
import hashlib
import io
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError  # type: ignore[import-untyped]

from app.offsite_backup import (
    MAX_SINGLE_PUT_SIZE,
    OffsiteBackupError,
    create_client,
    download_archive,
    load_config,
    main,
    object_key,
    upload_archive,
    verify_remote_object,
)

ARCHIVE_NAME = "photolamp-20261015T010203Z-v1-abcdef.dump.plb"
CHECKSUM = "a" * 64
REVISION = "b" * 40


def config_environment() -> dict[str, str]:
    return {
        "OFFSITE_BACKUP_ENDPOINT_URL": "https://storage.example.test",
        "OFFSITE_BACKUP_REGION": "test-1",
        "OFFSITE_BACKUP_BUCKET": "photolamp-backups",
        "OFFSITE_BACKUP_PREFIX": "staging/database",
        "OFFSITE_BACKUP_ADDRESSING_STYLE": "path",
        "OFFSITE_BACKUP_ACCESS_KEY_ID": "synthetic-access-key",
        "OFFSITE_BACKUP_SECRET_ACCESS_KEY": "synthetic-secret-key",
    }


def locked_head(payload: bytes, retain_until: datetime) -> dict[str, object]:
    return {
        "ContentLength": len(payload),
        "ChecksumSHA256": base64.b64encode(bytes.fromhex(CHECKSUM)).decode("ascii"),
        "Metadata": {"sha256": CHECKSUM, "revision": REVISION},
        "ObjectLockMode": "COMPLIANCE",
        "ObjectLockRetainUntilDate": retain_until,
    }


def test_load_config_rejects_unsafe_endpoint_and_missing_credentials() -> None:
    valid = config_environment()
    config = load_config(valid)
    assert object_key(config, ARCHIVE_NAME) == f"staging/database/{ARCHIVE_NAME}"

    invalid_values = (
        {**valid, "OFFSITE_BACKUP_ENDPOINT_URL": "http://storage.example.test"},
        {
            **valid,
            "OFFSITE_BACKUP_ENDPOINT_URL": "https://secret@storage.example.test",
        },
        {**valid, "OFFSITE_BACKUP_BUCKET": "Bad_Bucket"},
        {**valid, "OFFSITE_BACKUP_PREFIX": "../staging"},
        {**valid, "OFFSITE_BACKUP_SECRET_ACCESS_KEY": ""},
    )
    for environment in invalid_values:
        with pytest.raises(OffsiteBackupError):
            load_config(environment)


def test_client_supports_non_seekable_checksum_protected_streams() -> None:
    client = create_client(load_config(config_environment()))
    assert client.meta.config.signature_version == "s3v4"
    assert client.meta.config.s3["payload_signing_enabled"] is False


def test_upload_requires_compliance_lock_and_verifies_remote_head() -> None:
    config = load_config(config_environment())
    client = MagicMock()
    retain_until = datetime.now(UTC) + timedelta(days=30)
    payload = b"encrypted archive"
    client.head_object.return_value = locked_head(payload, retain_until)

    upload_archive(
        client,
        config,
        io.BytesIO(payload),
        archive_name=ARCHIVE_NAME,
        checksum=CHECKSUM,
        revision=REVISION,
        size=len(payload),
        retain_until=retain_until,
    )

    put = client.put_object.call_args.kwargs
    assert put["ObjectLockMode"] == "COMPLIANCE"
    assert put["IfNoneMatch"] == "*"
    assert put["Metadata"] == {"sha256": CHECKSUM, "revision": REVISION}
    assert "SecretAccessKey" not in repr(put)


def test_upload_rejects_archive_above_single_put_limit() -> None:
    config = load_config(config_environment())
    client = MagicMock()

    with pytest.raises(OffsiteBackupError, match="single-upload limit"):
        upload_archive(
            client,
            config,
            io.BytesIO(),
            archive_name=ARCHIVE_NAME,
            checksum=CHECKSUM,
            revision=REVISION,
            size=MAX_SINGLE_PUT_SIZE + 1,
            retain_until=datetime.now(UTC) + timedelta(days=30),
        )

    client.put_object.assert_not_called()


def test_verify_rejects_missing_lock_mismatched_metadata_or_checksum() -> None:
    config = load_config(config_environment())
    retain_until = datetime.now(UTC) + timedelta(days=30)
    client = MagicMock()
    valid_head = locked_head(b"encrypted archive", retain_until)

    for changed in (
        {**valid_head, "ObjectLockMode": "GOVERNANCE"},
        {**valid_head, "ObjectLockRetainUntilDate": datetime.now(UTC)},
        {**valid_head, "Metadata": "malformed"},
        {**valid_head, "Metadata": {"sha256": "c" * 64, "revision": REVISION}},
        {**valid_head, "ChecksumSHA256": "wrong"},
    ):
        client.head_object.return_value = changed
        with pytest.raises(OffsiteBackupError):
            verify_remote_object(
                client,
                config,
                archive_name=ARCHIVE_NAME,
                checksum=CHECKSUM,
                revision=REVISION,
                minimum_retain_until=retain_until - timedelta(minutes=1),
            )


def test_download_streams_only_expected_remote_archive() -> None:
    payload = b"downloaded encrypted archive"
    checksum = hashlib.sha256(payload).hexdigest()
    config = load_config(config_environment())
    retain_until = datetime.now(UTC) + timedelta(days=30)
    client = MagicMock()
    client.head_object.return_value = {
        "ContentLength": len(payload),
        "ChecksumSHA256": base64.b64encode(bytes.fromhex(checksum)).decode("ascii"),
        "Metadata": {"sha256": checksum, "revision": REVISION},
        "ObjectLockMode": "COMPLIANCE",
        "ObjectLockRetainUntilDate": retain_until,
    }
    client.get_object.return_value = {"Body": io.BytesIO(payload)}
    destination = io.BytesIO()

    download_archive(
        client,
        config,
        destination,
        archive_name=ARCHIVE_NAME,
        checksum=checksum,
        revision=REVISION,
        minimum_retain_until=retain_until - timedelta(minutes=1),
    )

    assert destination.getvalue() == payload


def test_download_detects_remote_payload_tampering() -> None:
    payload = b"expected encrypted archive"
    checksum = hashlib.sha256(payload).hexdigest()
    config = load_config(config_environment())
    retain_until = datetime.now(UTC) + timedelta(days=30)
    client = MagicMock()
    client.head_object.return_value = {
        "ContentLength": len(payload),
        "ChecksumSHA256": base64.b64encode(bytes.fromhex(checksum)).decode("ascii"),
        "Metadata": {"sha256": checksum, "revision": REVISION},
        "ObjectLockMode": "COMPLIANCE",
        "ObjectLockRetainUntilDate": retain_until,
    }
    client.get_object.return_value = {"Body": io.BytesIO(b"tampered remote payload")}

    with pytest.raises(OffsiteBackupError, match="integrity"):
        download_archive(
            client,
            config,
            io.BytesIO(),
            archive_name=ARCHIVE_NAME,
            checksum=checksum,
            revision=REVISION,
            minimum_retain_until=retain_until - timedelta(minutes=1),
        )


def test_cli_hides_provider_error_details(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    client = MagicMock()
    client.head_object.side_effect = ClientError(
        {
            "Error": {
                "Code": "AccessDenied",
                "Message": "do-not-print-provider-or-account-details",
            }
        },
        "HeadObject",
    )
    monkeypatch.setattr("app.offsite_backup.create_client", lambda _config: client)
    for name, value in config_environment().items():
        monkeypatch.setenv(name, value)

    result = main(
        [
            "verify",
            "--archive-name",
            ARCHIVE_NAME,
            "--checksum",
            CHECKSUM,
            "--revision",
            REVISION,
            "--retain-until",
            (datetime.now(UTC) + timedelta(days=29)).isoformat(),
        ]
    )

    output = capsys.readouterr()
    assert result == 1
    assert "S3-compatible storage operation failed" in output.err
    assert "do-not-print" not in output.err

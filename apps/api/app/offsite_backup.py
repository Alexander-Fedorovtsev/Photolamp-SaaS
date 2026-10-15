"""Minimal S3-compatible transport for encrypted, immutable backup archives."""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, BinaryIO, cast
from urllib.parse import urlsplit

import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]

ARCHIVE_NAME_PATTERN = re.compile(
    r"^photolamp-[0-9]{8}T[0-9]{6}Z-[a-z0-9][a-z0-9_-]{0,31}-[a-f0-9]{6}\.dump\.plb$"
)
BUCKET_PATTERN = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
PREFIX_PATTERN = re.compile(r"^[a-z0-9][a-z0-9/_-]{0,127}$")
REGION_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
REVISION_PATTERN = re.compile(r"^[a-f0-9]{40}$")
CHUNK_SIZE = 1024 * 1024
MAX_SINGLE_PUT_SIZE = 5 * 1024 * 1024 * 1024


class OffsiteBackupError(ValueError):
    """The off-site configuration or remote archive is invalid."""


@dataclass(frozen=True)
class OffsiteConfig:
    endpoint_url: str
    region: str
    bucket: str
    prefix: str
    addressing_style: str
    access_key_id: str
    secret_access_key: str


def load_config(environment: Mapping[str, str]) -> OffsiteConfig:
    endpoint_url = environment.get("OFFSITE_BACKUP_ENDPOINT_URL", "").strip()
    region = environment.get("OFFSITE_BACKUP_REGION", "").strip()
    bucket = environment.get("OFFSITE_BACKUP_BUCKET", "").strip()
    prefix = environment.get("OFFSITE_BACKUP_PREFIX", "").strip().strip("/")
    addressing_style = environment.get("OFFSITE_BACKUP_ADDRESSING_STYLE", "path").strip()
    access_key_id = environment.get("OFFSITE_BACKUP_ACCESS_KEY_ID", "").strip()
    secret_access_key = environment.get("OFFSITE_BACKUP_SECRET_ACCESS_KEY", "").strip()

    endpoint = urlsplit(endpoint_url)
    if (
        endpoint.scheme != "https"
        or not endpoint.hostname
        or endpoint.username
        or endpoint.password
        or endpoint.query
        or endpoint.fragment
    ):
        raise OffsiteBackupError("Off-site endpoint must be a credential-free HTTPS URL")
    if not REGION_PATTERN.fullmatch(region):
        raise OffsiteBackupError("Off-site region is invalid")
    if not BUCKET_PATTERN.fullmatch(bucket) or ".." in bucket or ".-" in bucket or "-." in bucket:
        raise OffsiteBackupError("Off-site bucket name is invalid")
    if not PREFIX_PATTERN.fullmatch(prefix) or "//" in prefix or ".." in prefix:
        raise OffsiteBackupError("Off-site object prefix is invalid")
    if addressing_style not in {"path", "virtual"}:
        raise OffsiteBackupError("Off-site addressing style must be path or virtual")
    if not access_key_id or not secret_access_key:
        raise OffsiteBackupError("Off-site credentials are not configured")

    return OffsiteConfig(
        endpoint_url=endpoint_url.rstrip("/"),
        region=region,
        bucket=bucket,
        prefix=prefix,
        addressing_style=addressing_style,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
    )


def parse_utc_timestamp(value: str) -> datetime:
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise OffsiteBackupError("Object-lock timestamp is invalid") from error
    if timestamp.tzinfo is None:
        raise OffsiteBackupError("Object-lock timestamp must include a timezone")
    return timestamp.astimezone(UTC)


def object_key(config: OffsiteConfig, archive_name: str) -> str:
    if not ARCHIVE_NAME_PATTERN.fullmatch(archive_name):
        raise OffsiteBackupError("Encrypted archive name is invalid")
    return f"{config.prefix}/{archive_name}"


def create_client(config: OffsiteConfig) -> Any:
    return boto3.client(
        "s3",
        endpoint_url=config.endpoint_url,
        region_name=config.region,
        aws_access_key_id=config.access_key_id,
        aws_secret_access_key=config.secret_access_key,
        config=Config(
            signature_version="s3v4",
            retries={"max_attempts": 3, "mode": "standard"},
            # The archive arrives on a non-seekable stdin pipe. TLS plus the
            # explicit x-amz-checksum-sha256 protects the streamed payload.
            s3={
                "addressing_style": config.addressing_style,
                "payload_signing_enabled": False,
            },
        ),
    )


def verify_remote_object(
    client: Any,
    config: OffsiteConfig,
    *,
    archive_name: str,
    checksum: str,
    revision: str,
    minimum_retain_until: datetime,
    expected_size: int | None = None,
) -> dict[str, Any]:
    if not SHA256_PATTERN.fullmatch(checksum):
        raise OffsiteBackupError("Encrypted archive checksum is invalid")
    if not REVISION_PATTERN.fullmatch(revision):
        raise OffsiteBackupError("Backup revision is invalid")
    if minimum_retain_until.tzinfo is None:
        raise OffsiteBackupError("Minimum retention must include a timezone")

    response = client.head_object(
        Bucket=config.bucket,
        Key=object_key(config, archive_name),
        ChecksumMode="ENABLED",
    )
    metadata = response.get("Metadata", {})
    if not isinstance(metadata, Mapping):
        raise OffsiteBackupError("Remote archive metadata is invalid")
    retain_until = response.get("ObjectLockRetainUntilDate")
    if (
        response.get("ObjectLockMode") != "COMPLIANCE"
        or not isinstance(retain_until, datetime)
        or retain_until.astimezone(UTC) < minimum_retain_until.astimezone(UTC)
    ):
        raise OffsiteBackupError("Remote archive does not have the required object lock")
    if metadata.get("sha256") != checksum or metadata.get("revision") != revision:
        raise OffsiteBackupError("Remote archive metadata does not match")
    expected_checksum = base64.b64encode(bytes.fromhex(checksum)).decode("ascii")
    if response.get("ChecksumSHA256") != expected_checksum:
        raise OffsiteBackupError("Remote archive checksum does not match")
    if expected_size is not None and response.get("ContentLength") != expected_size:
        raise OffsiteBackupError("Remote archive size does not match")
    return cast(dict[str, Any], response)


def upload_archive(
    client: Any,
    config: OffsiteConfig,
    source: BinaryIO,
    *,
    archive_name: str,
    checksum: str,
    revision: str,
    size: int,
    retain_until: datetime,
) -> None:
    if size <= 0:
        raise OffsiteBackupError("Encrypted archive size is invalid")
    if size > MAX_SINGLE_PUT_SIZE:
        raise OffsiteBackupError("Encrypted archive exceeds the single-upload limit")
    if retain_until <= datetime.now(UTC):
        raise OffsiteBackupError("Object-lock retention must end in the future")
    if not SHA256_PATTERN.fullmatch(checksum) or not REVISION_PATTERN.fullmatch(revision):
        raise OffsiteBackupError("Backup identity is invalid")

    client.put_object(
        Bucket=config.bucket,
        Key=object_key(config, archive_name),
        Body=source,
        ContentLength=size,
        ContentType="application/octet-stream",
        ChecksumAlgorithm="SHA256",
        ChecksumSHA256=base64.b64encode(bytes.fromhex(checksum)).decode("ascii"),
        Metadata={"sha256": checksum, "revision": revision},
        ObjectLockMode="COMPLIANCE",
        ObjectLockRetainUntilDate=retain_until,
        IfNoneMatch="*",
    )
    verify_remote_object(
        client,
        config,
        archive_name=archive_name,
        checksum=checksum,
        revision=revision,
        minimum_retain_until=retain_until,
        expected_size=size,
    )


def download_archive(
    client: Any,
    config: OffsiteConfig,
    destination: BinaryIO,
    *,
    archive_name: str,
    checksum: str,
    revision: str,
    minimum_retain_until: datetime,
) -> None:
    head = verify_remote_object(
        client,
        config,
        archive_name=archive_name,
        checksum=checksum,
        revision=revision,
        minimum_retain_until=minimum_retain_until,
    )
    response = client.get_object(Bucket=config.bucket, Key=object_key(config, archive_name))
    body = response.get("Body")
    if body is None:
        raise OffsiteBackupError("Remote archive response has no body")
    digest = hashlib.sha256()
    received_size = 0
    while chunk := body.read(CHUNK_SIZE):
        digest.update(chunk)
        destination.write(chunk)
        received_size += len(chunk)
    if digest.hexdigest() != checksum or received_size != head.get("ContentLength"):
        raise OffsiteBackupError("Downloaded archive failed integrity verification")


def parse_args(arguments: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("upload", "verify", "download"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--archive-name", required=True)
        subparser.add_argument("--checksum", required=True)
        subparser.add_argument("--revision", required=True)
        subparser.add_argument("--retain-until", required=True)
        if command == "upload":
            subparser.add_argument("--size", required=True, type=int)
    return parser.parse_args(arguments)


def main(arguments: list[str] | None = None) -> int:
    args = parse_args(arguments or sys.argv[1:])
    try:
        config = load_config(os.environ)
        client = create_client(config)
        retain_until = parse_utc_timestamp(args.retain_until)
        common = {
            "archive_name": args.archive_name,
            "checksum": args.checksum,
            "revision": args.revision,
        }
        if args.command == "upload":
            upload_archive(
                client,
                config,
                sys.stdin.buffer,
                size=args.size,
                retain_until=retain_until,
                **common,
            )
            print("[photolamp-offsite] Immutable encrypted archive uploaded and verified")
        elif args.command == "verify":
            verify_remote_object(
                client,
                config,
                minimum_retain_until=retain_until,
                **common,
            )
            print("[photolamp-offsite] Remote object lock and integrity metadata verified")
        else:
            download_archive(
                client,
                config,
                sys.stdout.buffer,
                minimum_retain_until=retain_until,
                **common,
            )
    except (
        OffsiteBackupError,
        BotoCoreError,
        ClientError,
        KeyError,
        OSError,
        TypeError,
    ) as error:
        if isinstance(error, OffsiteBackupError):
            message = str(error)
        else:
            message = "S3-compatible storage operation failed"
        print(f"[photolamp-offsite] {message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

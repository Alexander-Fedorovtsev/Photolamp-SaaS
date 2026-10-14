#!/usr/bin/env python3
"""Send a minimal operational alert to the configured Telegram destination."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

TELEGRAM_API_BASE_URL = "https://api.telegram.org"
BOT_TOKEN_PATTERN = re.compile(r"^[0-9]{6,15}:[A-Za-z0-9_-]{20,}$")
CHAT_ID_PATTERN = re.compile(r"^(?:-?[0-9]{1,20}|@[A-Za-z][A-Za-z0-9_]{4,31})$")
LABEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._/-]{0,79}$")
REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SHA_PATTERN = re.compile(r"^[a-f0-9]{40}$")


class AlertConfigurationError(ValueError):
    """The Telegram destination is absent or invalid."""


class AlertDeliveryError(RuntimeError):
    """Telegram rejected the alert or could not be reached."""


@dataclass(frozen=True)
class TelegramDestination:
    bot_token: str
    chat_id: str
    thread_id: int | None


def load_destination(environment: Mapping[str, str]) -> TelegramDestination:
    token = environment.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = environment.get("TELEGRAM_ALERT_CHAT_ID", "").strip()
    thread_value = environment.get("TELEGRAM_ALERT_THREAD_ID", "").strip()

    if not token or not chat_id:
        raise AlertConfigurationError(
            "Configure TELEGRAM_BOT_TOKEN and TELEGRAM_ALERT_CHAT_ID"
        )
    if not BOT_TOKEN_PATTERN.fullmatch(token):
        raise AlertConfigurationError("TELEGRAM_BOT_TOKEN has an invalid format")
    if not CHAT_ID_PATTERN.fullmatch(chat_id):
        raise AlertConfigurationError("TELEGRAM_ALERT_CHAT_ID has an invalid format")

    thread_id: int | None = None
    if thread_value:
        if not thread_value.isascii() or not thread_value.isdecimal():
            raise AlertConfigurationError(
                "TELEGRAM_ALERT_THREAD_ID must be a positive integer"
            )
        thread_id = int(thread_value)
        if thread_id <= 0:
            raise AlertConfigurationError(
                "TELEGRAM_ALERT_THREAD_ID must be a positive integer"
            )

    return TelegramDestination(token, chat_id, thread_id)


def build_message(
    *,
    status: str,
    component: str,
    environment: str,
    repository: str,
    run_url: str,
    sha: str,
    observed_at: datetime | None = None,
) -> str:
    if status not in {"failure", "test"}:
        raise ValueError("Unsupported alert status")
    if not LABEL_PATTERN.fullmatch(component):
        raise ValueError("Invalid component label")
    if not LABEL_PATTERN.fullmatch(environment):
        raise ValueError("Invalid environment label")
    if not REPOSITORY_PATTERN.fullmatch(repository):
        raise ValueError("Invalid repository name")
    if not SHA_PATTERN.fullmatch(sha):
        raise ValueError("Invalid Git SHA")
    if not run_url.startswith(f"https://github.com/{repository}/actions/runs/"):
        raise ValueError("Run URL must point to this repository's GitHub Actions")

    timestamp = (observed_at or datetime.now(UTC)).astimezone(UTC)
    if status == "failure":
        heading = "🔴 Photolamp: требуется внимание"
        state = "Проверка завершилась с ошибкой"
    else:
        heading = "🟢 Photolamp: тест on-call канала"
        state = "Тестовое уведомление доставлено"

    return "\n".join(
        (
            heading,
            state,
            f"Среда: {environment}",
            f"Компонент: {component}",
            f"Ревизия: {sha[:12]}",
            f"Время UTC: {timestamp:%Y-%m-%d %H:%M:%S}",
            f"GitHub Actions: {run_url}",
        )
    )


def send_message(
    destination: TelegramDestination,
    message: str,
    *,
    api_base_url: str = TELEGRAM_API_BASE_URL,
    timeout_seconds: float = 10,
) -> None:
    payload: dict[str, object] = {
        "chat_id": destination.chat_id,
        "text": message,
        "disable_web_page_preview": True,
    }
    if destination.thread_id is not None:
        payload["message_thread_id"] = destination.thread_id

    request = urllib.request.Request(
        f"{api_base_url}/bot{destination.bot_token}/sendMessage",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            response_body = response.read(65536)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as error:
        raise AlertDeliveryError("Telegram API request failed") from error

    try:
        result = json.loads(response_body)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise AlertDeliveryError("Telegram API returned an invalid response") from error
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise AlertDeliveryError("Telegram API rejected the alert")


def parse_args(arguments: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", required=True, choices=("failure", "test"))
    parser.add_argument("--component", required=True)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--sha", required=True)
    return parser.parse_args(arguments)


def main(arguments: list[str] | None = None) -> int:
    args = parse_args(arguments or sys.argv[1:])
    try:
        destination = load_destination(os.environ)
        message = build_message(
            status=args.status,
            component=args.component,
            environment=args.environment,
            repository=args.repository,
            run_url=args.run_url,
            sha=args.sha,
        )
        send_message(destination, message)
    except (AlertConfigurationError, AlertDeliveryError, ValueError) as error:
        print(f"[photolamp-alert] {error}", file=sys.stderr)
        return 1

    print("[photolamp-alert] Telegram alert delivered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

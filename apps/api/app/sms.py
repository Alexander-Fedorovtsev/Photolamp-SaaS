"""SMS provider boundary with an explicit local-only memory adapter."""

from typing import Protocol
from uuid import UUID

from app.config import Settings


class SmsProvider(Protocol):
    @property
    def enabled(self) -> bool: ...

    async def send_otp(self, challenge_id: UUID, destination: str, code: str) -> None: ...


class DisabledSmsProvider:
    @property
    def enabled(self) -> bool:
        return False

    async def send_otp(self, challenge_id: UUID, destination: str, code: str) -> None:
        del challenge_id, destination, code
        raise RuntimeError("SMS provider is disabled")


class MemorySmsProvider:
    """Keeps only codes, never destinations; valid only in development and tests."""

    def __init__(self) -> None:
        self._codes: dict[UUID, str] = {}

    @property
    def enabled(self) -> bool:
        return True

    async def send_otp(self, challenge_id: UUID, destination: str, code: str) -> None:
        del destination
        self._codes[challenge_id] = code

    def code_for(self, challenge_id: UUID) -> str | None:
        return self._codes.get(challenge_id)


def build_sms_provider(settings: Settings) -> SmsProvider:
    if settings.sms_provider_mode == "memory":
        return MemorySmsProvider()
    return DisabledSmsProvider()

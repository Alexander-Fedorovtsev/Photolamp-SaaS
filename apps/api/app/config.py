from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.contact_crypto import parse_contact_keyring

LOCAL_CONTACT_KEY = "cGhvdG9sYW1wLWxvY2FsLXN5bnRoZXRpYy1rZXktdjE="
LOCAL_CONTACT_KEYRING = '{"1":"' + LOCAL_CONTACT_KEY + '"}'


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["development", "test", "staging", "production"] = "development"
    photolamp_deploy_sha: str = Field(
        default="development", pattern=r"^(development|[a-f0-9]{40})$"
    )
    database_url: SecretStr = Field(
        default=SecretStr(
            "postgresql+asyncpg://photolamp:local-development-only@localhost:5432/photolamp"
        )
    )
    database_owner_url: SecretStr | None = None
    redis_url: SecretStr = SecretStr("redis://localhost:6379/0")
    enable_demo_previews: bool = False
    dependency_timeout_seconds: float = Field(default=2.0, gt=0, le=10)
    hold_ttl_seconds: int = Field(default=600, ge=60, le=1800)
    checkout_session_ttl_seconds: int = Field(default=1800, ge=300, le=3600)
    checkout_rate_limit_window_seconds: int = Field(default=60, ge=10, le=300)
    checkout_session_rate_limit: int = Field(default=10, ge=1, le=100)
    checkout_mutation_rate_limit: int = Field(default=10, ge=1, le=100)
    sms_provider_mode: Literal["disabled", "memory"] = "disabled"
    otp_pepper: SecretStr = SecretStr("local-synthetic-otp-pepper-change-me")
    contact_active_key_version: int = Field(default=1, ge=1, le=32767)
    contact_encryption_keys: SecretStr = SecretStr(LOCAL_CONTACT_KEYRING)
    otp_ttl_seconds: int = Field(default=300, ge=120, le=600)
    otp_max_attempts: int = Field(default=5, ge=3, le=10)
    otp_request_rate_limit: int = Field(default=3, ge=1, le=20)
    otp_verify_rate_limit: int = Field(default=10, ge=3, le=50)
    customer_session_ttl_seconds: int = Field(default=86400, ge=900, le=604800)
    staff_session_ttl_seconds: int = Field(default=43200, ge=900, le=604800)
    staff_invitation_ttl_seconds: int = Field(default=172800, ge=3600, le=604800)
    staff_auth_rate_limit: int = Field(default=10, ge=3, le=50)

    @model_validator(mode="after")
    def validate_environment(self) -> "Settings":
        if not self.database_url.get_secret_value().startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must use PostgreSQL with the asyncpg driver")
        if self.database_owner_url and not self.database_owner_url.get_secret_value().startswith(
            "postgresql+asyncpg://"
        ):
            raise ValueError("DATABASE_OWNER_URL must use PostgreSQL with the asyncpg driver")
        if self.app_env in {"staging", "production"}:
            if "local-development-only" in self.database_url.get_secret_value():
                raise ValueError("Configure deployment database credentials")
            if self.photolamp_deploy_sha == "development":
                raise ValueError("Deployments require an exact Git SHA")
        if self.app_env == "production":
            if self.enable_demo_previews:
                raise ValueError("Demo previews must be disabled in production")
            if LOCAL_CONTACT_KEY in self.contact_encryption_keys.get_secret_value():
                raise ValueError("Configure a production contact encryption key")
            if self.otp_pepper.get_secret_value() == "local-synthetic-otp-pepper-change-me":
                raise ValueError("Configure a production phone lookup pepper")
        if self.sms_provider_mode == "memory" and self.app_env not in {"development", "test"}:
            raise ValueError("The memory SMS provider is allowed only in development and tests")
        if (
            self.sms_provider_mode != "disabled"
            and self.app_env in {"staging", "production"}
            and self.otp_pepper.get_secret_value() == "local-synthetic-otp-pepper-change-me"
        ):
            raise ValueError("Configure a non-development OTP pepper")
        keyring = parse_contact_keyring(self.contact_encryption_keys.get_secret_value())
        if self.contact_active_key_version not in keyring:
            raise ValueError("CONTACT_ENCRYPTION_KEYS must contain the active key version")
        return self

    def contact_keyring(self) -> dict[int, str]:
        return parse_contact_keyring(self.contact_encryption_keys.get_secret_value())

    def migration_database_url(self) -> str:
        """Owner credentials are available only to bootstrap/migration processes."""
        source = self.database_owner_url or self.database_url
        return source.get_secret_value()

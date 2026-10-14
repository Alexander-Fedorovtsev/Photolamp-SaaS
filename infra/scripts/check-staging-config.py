"""Validate a resolved Compose model; never print credential values."""

import json
import re
import sys
from urllib.parse import urlsplit


class StagingConfigError(ValueError):
    """A deliberately credential-free explanation for the operator."""


def validate(model: dict) -> None:
    def require(condition: bool, message: str) -> None:
        if not condition:
            raise StagingConfigError(message)

    require(model.get("name") == "photolamp-test", "Unexpected staging project name")
    services = model["services"]
    require(
        set(services)
        == {
            "postgres",
            "redis",
            "db-bootstrap",
            "migrate",
            "api",
            "backup-crypto",
            "contact-maintenance",
            "web",
        },
        "Unexpected staging services",
    )
    database = services["postgres"]["environment"]
    password = database["POSTGRES_PASSWORD"]
    require(
        bool(re.fullmatch(r"[a-fA-F0-9]{64}", password)),
        "Use a 64-character random hex database password",
    )
    for name, service in services.items():
        require(not service.get("ports"), f"Host ports must not be published by {name}")
        if name == "backup-crypto":
            require(
                service.get("network_mode") == "none" and not service.get("networks"),
                "Backup cryptography must not have network access",
            )
            continue
        networks = set(service["networks"])
        require(
            networks == ({"internal", "traefik"} if name == "web" else {"internal"}),
            "Unexpected network exposure",
        )
    runtime_url = urlsplit(services["api"]["environment"]["DATABASE_URL"])
    runtime_password = runtime_url.password or ""
    require(
        runtime_url.username == "photolamp_runtime"
        and bool(re.fullmatch(r"[a-fA-F0-9]{64}", runtime_password))
        and runtime_password != password,
        "Use a separate 64-character runtime database password",
    )
    for name in ("api", "migrate", "db-bootstrap", "contact-maintenance"):
        env = services[name]["environment"]
        require(
            env["APP_ENV"] == "staging"
            and str(env["ENABLE_DEMO_PREVIEWS"]).lower() == "true",
            "Staging must be an explicit synthetic demo",
        )
        expected_runtime_url = f"postgresql+asyncpg://photolamp_runtime:{runtime_password}@postgres:5432/{database['POSTGRES_DB']}"
        require(
            env["DATABASE_URL"] == expected_runtime_url,
            "Runtime database configuration does not match",
        )
        if name in {"migrate", "db-bootstrap", "contact-maintenance"}:
            expected_owner_url = f"postgresql+asyncpg://{database['POSTGRES_USER']}:{password}@postgres:5432/{database['POSTGRES_DB']}"
            require(
                env.get("DATABASE_OWNER_URL") == expected_owner_url,
                "Owner database configuration does not match",
            )
        else:
            require(
                "DATABASE_OWNER_URL" not in env,
                "API must not receive database owner credentials",
            )
    contact_environment = services["api"]["environment"]
    active_contact_version = str(
        contact_environment.get("CONTACT_ACTIVE_KEY_VERSION", "")
    )
    try:
        contact_keyring = json.loads(
            contact_environment.get("CONTACT_ENCRYPTION_KEYS", "")
        )
    except json.JSONDecodeError:
        contact_keyring = None
    valid_contact_keys = (
        isinstance(contact_keyring, dict)
        and 1 <= len(contact_keyring) <= 8
        and active_contact_version in contact_keyring
        and all(
            isinstance(version, str)
            and version.isdecimal()
            and version == str(int(version))
            and 1 <= int(version) <= 32767
            and isinstance(key, str)
            and bool(re.fullmatch(r"[A-Za-z0-9_-]{43}=", key))
            for version, key in contact_keyring.items()
        )
        and len(set(contact_keyring.values())) == len(contact_keyring)
    )
    require(valid_contact_keys, "Configure a valid versioned contact keyring")
    require(
        services["contact-maintenance"]["environment"].get("CONTACT_ACTIVE_KEY_VERSION")
        == contact_environment.get("CONTACT_ACTIVE_KEY_VERSION")
        and services["contact-maintenance"]["environment"].get(
            "CONTACT_ENCRYPTION_KEYS"
        )
        == contact_environment.get("CONTACT_ENCRYPTION_KEYS")
        and all(
            "CONTACT_ENCRYPTION_KEY" not in services[name].get("environment", {})
            for name in services
        ),
        "Contact keyring must match the isolated maintenance process",
    )
    backup_environment = services["backup-crypto"]["environment"]
    active_backup_key = backup_environment.get("BACKUP_ACTIVE_KEY_ID", "")
    try:
        backup_keyring = json.loads(
            backup_environment.get("BACKUP_ENCRYPTION_KEYS", "")
        )
    except json.JSONDecodeError:
        backup_keyring = None
    require(
        bool(re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,31}", active_backup_key))
        and isinstance(backup_keyring, dict)
        and active_backup_key in backup_keyring
        and all(
            bool(re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,31}", key_id))
            and isinstance(key, str)
            and bool(re.fullmatch(r"[a-fA-F0-9]{64}", key))
            for key_id, key in backup_keyring.items()
        ),
        "Configure a valid versioned backup keyring",
    )
    require(
        set(backup_environment) == {"BACKUP_ACTIVE_KEY_ID", "BACKUP_ENCRYPTION_KEYS"}
        and all(
            not any(
                key.startswith("BACKUP_")
                for key in services[name].get("environment", {})
            )
            for name in services
            if name != "backup-crypto"
        ),
        "Backup keys must be isolated from application containers",
    )
    labels = services["web"]["labels"]
    require(
        services["web"]["environment"]["APP_ENV"] == "staging"
        and services["web"]["environment"]["API_INTERNAL_URL"]
        == "http://photolamp-test-backend:8000"
        and "photolamp-test-backend"
        in services["api"]["networks"]["internal"].get("aliases", []),
        "Frontend must enforce staging security and use the private API alias",
    )
    require(
        labels["traefik.http.routers.photolamp-test.rule"]
        == "Host(`photolamp-test.anliwei.pro`)",
        "Unexpected hostname",
    )
    # `compose config` escapes dollar signs for re-serialization; runtime labels do not.
    auth = labels[
        "traefik.http.middlewares.photolamp-test-auth.basicauth.users"
    ].replace("$$", "$")
    require(
        bool(re.fullmatch(r"[a-zA-Z0-9_.-]+:\$2[aby]\$\d{2}\$[./a-zA-Z0-9]{53}", auth)),
        "Basic Auth must contain one intact bcrypt hash",
    )
    require(
        labels["traefik.http.routers.photolamp-test.middlewares"]
        == "photolamp-test-auth@docker",
        "Basic Auth must protect all public routes",
    )
    require(
        labels["traefik.http.routers.photolamp-test.tls"] == "true",
        "HTTPS must be enabled",
    )
    require(
        model["networks"]["internal"]["internal"] is True,
        "Private network must be internal",
    )
    for name in (
        "api",
        "web",
        "migrate",
        "db-bootstrap",
        "backup-crypto",
        "contact-maintenance",
    ):
        sha = services[name]["build"]["args"]["PHOTOLAMP_DEPLOY_SHA"]
        require(
            bool(re.fullmatch(r"[a-f0-9]{40}", sha)), "Images require an exact Git SHA"
        )
        require(
            services[name]["image"].endswith(":" + sha),
            "Image tag must match source SHA",
        )
    require(
        services["api"]["image"]
        == services["migrate"]["image"]
        == services["backup-crypto"]["image"]
        == services["contact-maintenance"]["image"],
        "API and maintenance processes must use the same image",
    )
    require(
        services["postgres"]["volumes"][0]["source"]
        == "/mnt/raid/photolamp-test/postgres",
        "Unexpected data location",
    )
    print(
        "Staging config passed: isolated ports/networks, credentials, TLS, auth and image revision"
    )


if __name__ == "__main__":
    try:
        validate(json.load(sys.stdin))
    except StagingConfigError as error:
        raise SystemExit(f"Invalid staging Compose configuration: {error}") from None
    except (KeyError, ValueError, TypeError):
        # Avoid traceback, model repr or values containing credentials.
        raise SystemExit(
            "Invalid staging Compose configuration; consult docs/deployment.md"
        ) from None

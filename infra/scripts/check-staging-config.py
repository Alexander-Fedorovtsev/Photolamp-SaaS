"""Validate a resolved Compose model; never print credential values."""

import json
import re
import sys


class StagingConfigError(ValueError):
    """A deliberately credential-free explanation for the operator."""


def validate(model: dict) -> None:
    def require(condition: bool, message: str) -> None:
        if not condition:
            raise StagingConfigError(message)

    require(model.get("name") == "photolamp-test", "Unexpected staging project name")
    services = model["services"]
    require(
        set(services) == {"postgres", "redis", "migrate", "api", "web"},
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
        networks = set(service["networks"])
        require(
            networks == ({"internal", "traefik"} if name == "web" else {"internal"}),
            "Unexpected network exposure",
        )
    for name in ("api", "migrate"):
        env = services[name]["environment"]
        require(
            env["APP_ENV"] == "staging"
            and str(env["ENABLE_DEMO_PREVIEWS"]).lower() == "true",
            "Staging must be an explicit synthetic demo",
        )
        expected_url = f"postgresql+asyncpg://{database['POSTGRES_USER']}:{password}@postgres:5432/{database['POSTGRES_DB']}"
        require(
            env["DATABASE_URL"] == expected_url, "Database configuration does not match"
        )
    labels = services["web"]["labels"]
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
    for name in ("api", "web", "migrate"):
        sha = services[name]["build"]["args"]["PHOTOLAMP_DEPLOY_SHA"]
        require(
            bool(re.fullmatch(r"[a-f0-9]{40}", sha)), "Images require an exact Git SHA"
        )
        require(
            services[name]["image"].endswith(":" + sha),
            "Image tag must match source SHA",
        )
    require(
        services["api"]["image"] == services["migrate"]["image"],
        "API and migration must use the same image",
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

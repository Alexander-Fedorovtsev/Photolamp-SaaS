"""Exercise an already running real stack, without external dependencies."""

import json
import os
from urllib.error import HTTPError
from urllib.request import urlopen

api_url = os.environ.get("SMOKE_API_URL", "http://127.0.0.1:8000")
web_url = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:3000")


def get(url: str) -> tuple[int, str]:
    try:
        with urlopen(url, timeout=10) as response:
            return response.status, response.read().decode()
    except HTTPError as error:
        return error.code, error.read().decode()


for base, health_path in [(api_url, "/api/v1/health/ready"), (web_url, "/api/health/ready")]:
    status, body = get(base + health_path)
    assert status == 200, (health_path, status)
    assert json.loads(body)["status"] == "ready"

for slug, city in [("demo-ufa", "Уфа"), ("demo-kazan", "Казань")]:
    status, body = get(f"{api_url}/api/v1/r/{slug}/preview")
    preview = json.loads(body)
    assert status == 200 and preview["slug"] == slug and preview["city"] == city
    assert preview["is_demo"] is True and preview["booking_enabled"] is False
    status, body = get(f"{web_url}/r/{slug}")
    assert status == 200 and preview["display_name"] in body

assert get(f"{api_url}/api/v1/r/unknown/preview")[0] == 404
assert get(f"{web_url}/r/unknown")[0] == 404
assert get(f"{api_url}/api/v1/me/tenants")[0] == 401
assert get(f"{api_url}/openapi.json")[0] == 200
assert get(f"{web_url}/manifest.webmanifest")[0] == 200
print(
    "Stack smoke: readiness, two previews, auth boundary, unknown tenant, "
    "OpenAPI and manifest passed"
)

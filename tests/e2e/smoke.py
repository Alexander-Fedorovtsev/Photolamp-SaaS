"""Exercise an already running real stack, without external dependencies."""

import json
import os
from datetime import date, timedelta
from urllib.error import HTTPError
from urllib.request import Request, urlopen

api_url = os.environ.get("SMOKE_API_URL", "http://127.0.0.1:8000")
web_url = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:3000")


def request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
) -> tuple[int, str]:
    try:
        with urlopen(
            Request(url, method=method, headers=headers or {}, data=data), timeout=10
        ) as response:
            return response.status, response.read().decode()
    except HTTPError as error:
        return error.code, error.read().decode()


def get(url: str) -> tuple[int, str]:
    return request(url)


for base, health_path in [
    (api_url, "/api/v1/health/ready"),
    (web_url, "/api/health/ready"),
]:
    status, body = get(base + health_path)
    assert status == 200, (health_path, status)
    assert json.loads(body)["status"] == "ready"

for slug, city in [("demo-ufa", "Уфа"), ("demo-kazan", "Казань")]:
    status, body = get(f"{api_url}/api/v1/r/{slug}/catalog")
    catalog = json.loads(body)
    assert status == 200 and catalog["slug"] == slug and catalog["city"] == city
    assert catalog["is_demo"] is True and catalog["booking_enabled"] is False
    assert catalog["availability_confirmed"] is False and catalog["items"]
    assert all(item["price_is_demo"] is True for item in catalog["items"])
    start = date.today() + timedelta(days=10)
    end = start + timedelta(days=2)
    quote_request = {
        "model_id": catalog["items"][0]["id"],
        "branch_id": catalog["branches"][0]["id"],
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "fulfillment": "pickup",
    }
    status, body = request(
        f"{api_url}/api/v1/r/{slug}/checkout/quote",
        method="POST",
        headers={"Content-Type": "application/json"},
        data=json.dumps(quote_request).encode(),
    )
    quote = json.loads(body)
    assert status == 200 and quote["days"] == 3
    assert quote["availability_confirmed"] is True
    assert quote["reservation_created"] is False and quote["booking_enabled"] is False
    assert quote["rental_amount_minor"] == quote["amount_minor_per_day"] * 3
    status, body = request(
        f"{api_url}/api/v1/r/{slug}/checkout/sessions",
        method="POST",
    )
    assert status == 409 and json.loads(body) == {"detail": "Booking is not enabled"}
    assert "checkout_token" not in body
    status, body = get(f"{web_url}/r/{slug}")
    assert status == 200 and catalog["display_name"] in body

assert get(f"{api_url}/api/v1/r/unknown/catalog")[0] == 404
assert get(f"{web_url}/r/unknown")[0] == 404
assert get(f"{api_url}/api/v1/me/tenants")[0] == 401
status, body = request(f"{api_url}/api/v1/auth/demo-session", method="POST")
assert status == 200
demo_session = json.loads(body)
assert demo_session["demo_only"] is True
authorization = {"Authorization": f"Bearer {demo_session['access_token']}"}
status, body = request(f"{api_url}/api/v1/me/tenants", headers=authorization)
memberships = json.loads(body)
assert status == 200 and {item["slug"] for item in memberships} == {
    "demo-ufa",
    "demo-kazan",
}
tenant_id = next(
    item["tenant_id"] for item in memberships if item["slug"] == "demo-ufa"
)
status, body = request(
    f"{api_url}/api/v1/t/{tenant_id}/catalog-admin", headers=authorization
)
admin_catalog = json.loads(body)
assert status == 200 and admin_catalog["tenant_id"] == tenant_id
assert admin_catalog["role"] == "owner" and admin_catalog["assets"]
status, body = request(f"{api_url}/api/v1/t/{tenant_id}/team", headers=authorization)
team = json.loads(body)
assert status == 200 and team["tenant_id"] == tenant_id and team["can_manage"] is True
assert len(team["members"]) >= 3 and "phone" not in body
status, body = request(f"{api_url}/api/v1/auth/staff/sessions", headers=authorization)
sessions = json.loads(body)
assert status == 200 and len(sessions) == 1 and sessions[0]["is_current"] is True
assert "token" not in body.lower() and "user_agent" not in body.lower()
status, body = request(
    f"{api_url}/api/v1/t/{tenant_id}/team/invitations", headers=authorization
)
assert status == 200 and "invitation_token" not in body and "password" not in body
assert (
    request(f"{api_url}/api/v1/auth/session", method="DELETE", headers=authorization)[0]
    == 200
)
assert get(f"{api_url}/openapi.json")[0] == 200
assert get(f"{web_url}/manifest.webmanifest")[0] == 200
print(
    "Stack smoke: readiness, two catalogs and quotes, blocked demo checkout, demo staff "
    "session, privacy-safe session inventory, tenant admin/team invitation boundary, "
    "auth boundary, unknown tenant, "
    "OpenAPI and manifest passed"
)

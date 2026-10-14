import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_request_id_is_preserved_or_safely_replaced() -> None:
    application = create_app(Settings(app_env="test", enable_demo_previews=True))
    with TestClient(application, raise_server_exceptions=False) as client:
        supplied = "monitor-12345678"
        response = client.get("/api/v1/health/live", headers={"X-Request-ID": supplied})
        assert response.headers["x-request-id"] == supplied

        replaced = client.get("/api/v1/health/live", headers={"X-Request-ID": "bad id"})
        assert replaced.headers["x-request-id"] != "bad id"
        assert len(replaced.headers["x-request-id"]) == 32


def test_metrics_use_route_templates_and_exclude_request_values() -> None:
    application = create_app(Settings(app_env="test", enable_demo_previews=True))
    with TestClient(application, raise_server_exceptions=False) as client:
        client.get("/api/v1/r/demo-ufa/catalog")
        client.get("/api/v1/r/demo-kazan/catalog")
        response = client.get("/internal/metrics")
        body = response.text
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/plain; version=0.0.4; charset=utf-8"
        assert "photolamp_build_info" in body
        assert 'route="/api/v1/r/{tenant_slug}/catalog"' in body
        assert "demo-ufa" not in body
        assert "demo-kazan" not in body
        assert "photolamp_http_request_duration_seconds" in body
        assert "photolamp_http_requests_in_progress 1.0" in body


def test_unhandled_exceptions_are_counted_without_disclosing_details(
    capsys: pytest.CaptureFixture[str],
) -> None:
    application = create_app(Settings(app_env="test", enable_demo_previews=True))

    async def explode() -> None:
        raise RuntimeError("password=must-never-reach-response-or-metrics")

    application.add_api_route("/synthetic-error", explode)
    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.get("/synthetic-error")
        assert response.status_code == 500
        assert response.json() == {"detail": "Internal server error"}
        assert len(response.headers["x-request-id"]) == 32
        metrics = client.get("/internal/metrics").text
    assert "password=must-never-reach-response-or-metrics" not in metrics
    logs = capsys.readouterr().out
    assert "password=must-never-reach-response-or-metrics" not in logs
    assert '"exception_type":"RuntimeError"' in logs
    assert (
        'photolamp_http_exceptions_total{exception_type="RuntimeError",method="GET",'
        'route="/synthetic-error"} 1.0'
    ) in metrics

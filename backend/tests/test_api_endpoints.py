"""
tests/test_api_endpoints.py
---------------------------
Integration tests for FastAPI endpoints, authentication, and compliance reports.
Uses pytest fixture with lifespan context manager so DB tables and seed data initialize.
"""

import pytest
from fastapi.testclient import TestClient
from main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_health_check(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "SYMBIOS" in data["service"]


def test_list_zones(client):
    response = client.get("/api/v1/zones")
    assert response.status_code == 200
    zones = response.json()
    assert len(zones) >= 1
    assert zones[0]["name"] == "Robotic Arm Envelope"
    assert len(zones[0]["polygon_coordinates"]) >= 3


def test_list_cobots(client):
    response = client.get("/api/v1/cobots")
    assert response.status_code == 200
    cobots = response.json()
    assert len(cobots) >= 1
    assert cobots[0]["model"] == "Universal Robots UR10e"


from core.security import get_bootstrap_admin_password


def test_auth_login_success(client):
    payload = {
        "username": "admin@symbios.ai",
        "password": get_bootstrap_admin_password()
    }
    response = client.post("/api/v1/auth/token", data=payload)
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["role"] == "admin"


def test_auth_login_failure(client):
    payload = {
        "username": "admin@symbios.ai",
        "password": "wrongpassword"
    }
    response = client.post("/api/v1/auth/token", data=payload)
    assert response.status_code == 401


def test_analytics_summary(client):
    response = client.get("/api/v1/analytics/summary")
    assert response.status_code == 200
    data = response.json()
    assert "active_cameras" in data
    assert "tracked_workers" in data
    assert "active_zones" in data


def test_explain_endpoint_fallback(client):
    response = client.post("/api/v1/explain", json={"use_llm": False})
    assert response.status_code == 200
    data = response.json()
    assert "explanation" in data
    assert len(data["explanation"]) > 20
    assert data["source"] in ["deterministic_template", "deterministic_fallback"]


def test_incident_report_export(client):
    response = client.get("/api/v1/explain/incident-report")
    assert response.status_code == 200
    report = response.json()
    assert "report_metadata" in report
    assert "telemetry_metrics" in report
    assert "ISO 45001" in report["report_metadata"]["standard_compliance"]

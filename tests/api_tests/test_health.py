"""Tests for the ``/health`` endpoint."""

from dataregistry_api import database
from dataregistry_api.app import APP


def test_health_returns_healthy(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_health_fails_when_the_database_is_unreachable():
    """A broken connection must surface as an error, not a healthy response."""

    def broken_engine():
        raise RuntimeError("database is down")

    APP.dependency_overrides[database.get_engine] = broken_engine
    try:
        from fastapi.testclient import TestClient

        with TestClient(APP, raise_server_exceptions=False) as test_client:
            response = test_client.get("/health")
        assert response.status_code == 500
    finally:
        APP.dependency_overrides.clear()

"""Tests for the ``/health`` endpoint."""

import pytest
from sqlalchemy import text

from dataregistry_api import database
from dataregistry_api.routes import APP


def test_health_returns_healthy(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_health_uses_a_live_connection(client, connection):
    """The endpoint runs its query on a real, usable connection.

    The fixture hands the endpoint the same connection the test holds, so if
    the endpoint had silently skipped the database this assertion on the
    shared transaction would not be meaningful. Instead we verify the
    connection the app was given can genuinely round-trip a query.
    """
    assert client.get("/health").status_code == 200
    assert connection.execute(text("SELECT 1")).scalar_one() == 1


def test_health_fails_when_the_database_is_unreachable():
    """A broken connection must surface as an error, not a healthy response."""

    def broken_connection():
        raise RuntimeError("database is down")
        yield  # pragma: no cover - makes this a generator dependency

    APP.dependency_overrides[database.get_database_connection] = broken_connection
    try:
        from fastapi.testclient import TestClient

        with TestClient(APP, raise_server_exceptions=False) as test_client:
            response = test_client.get("/health")
        assert response.status_code == 500
    finally:
        APP.dependency_overrides.clear()


@pytest.mark.parametrize("method", ["post", "put", "delete"])
def test_health_rejects_other_methods(client, method):
    response = getattr(client, method)("/health")

    assert response.status_code == 405

"""Authentication boundary tests for Globus-protected API routes."""

from fastapi.testclient import TestClient

from dataregistry_api import auth
from dataregistry_api.app import APP
from dataregistry_api.config import GlobusAuthSettings
from dataregistry_api.errors import AuthenticationError, AuthorizationError


def _settings() -> GlobusAuthSettings:
    return GlobusAuthSettings(
        globus_auth_client_id="resource-server-client",
        globus_auth_client_secret="test-secret",
        globus_auth_expected_audience="dataregistry-api",
        globus_auth_required_scope="dataregistry:read",
    )


def _metadata(**overrides):
    metadata = {
        "active": True,
        "token_type": "Bearer",
        "iss": "https://auth.globus.org/",
        "aud": ["dataregistry-api"],
        "scope": "dataregistry:read",
        "sub": "globus-identity-id",
        "username": "reader@example.org",
        "identity_set": ["globus-identity-id", "linked-identity-id"],
    }
    metadata.update(overrides)
    return metadata


def test_protected_route_requires_a_bearer_token():
    APP.dependency_overrides.clear()
    try:
        with TestClient(APP) as client:
            response = client.post("/datasets/query", json={})
    finally:
        APP.dependency_overrides.clear()

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


def test_non_bearer_authorization_is_rejected():
    APP.dependency_overrides.clear()
    try:
        with TestClient(APP) as client:
            response = client.post(
                "/datasets/query", json={}, headers={"Authorization": "Basic ignored"}
            )
    finally:
        APP.dependency_overrides.clear()

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


def test_health_is_public(client):
    response = client.get("/health")

    assert response.status_code == 200


def test_valid_introspection_response_becomes_a_principal():
    principal = auth._principal_from_token_metadata(_metadata(), _settings())

    assert principal.subject == "globus-identity-id"
    assert principal.scopes == frozenset({"dataregistry:read"})
    assert principal.identities == frozenset({"globus-identity-id", "linked-identity-id"})


def test_inactive_token_is_unauthenticated():
    try:
        auth._principal_from_token_metadata(_metadata(active=False), _settings())
    except AuthenticationError as exc:
        assert exc.code == "UNAUTHENTICATED"
    else:
        raise AssertionError("inactive token was accepted")


def test_wrong_audience_is_unauthenticated():
    try:
        auth._principal_from_token_metadata(_metadata(aud=["another-api"]), _settings())
    except AuthenticationError:
        pass
    else:
        raise AssertionError("wrong audience was accepted")


def test_wrong_issuer_is_unauthenticated():
    try:
        auth._principal_from_token_metadata(_metadata(iss="https://issuer.invalid"), _settings())
    except AuthenticationError:
        pass
    else:
        raise AssertionError("wrong issuer was accepted")


def test_missing_read_scope_is_forbidden():
    try:
        auth._principal_from_token_metadata(_metadata(scope="other:scope"), _settings())
    except AuthorizationError as exc:
        assert exc.code == "INSUFFICIENT_SCOPE"
    else:
        raise AssertionError("token without read scope was accepted")

"""Globus Auth resource-server authentication for the HTTP API."""

from dataclasses import dataclass
from typing import Annotated, Any

import globus_sdk
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from dataregistry_api.config import GlobusAuthSettings
from dataregistry_api.errors import AuthenticationError, AuthorizationError


BearerCredentials = Annotated[
    HTTPAuthorizationCredentials | None,
    Depends(
        HTTPBearer(
            auto_error=False,
            scheme_name="bearerAuth",
            description=(
                "Globus Auth access token for the DataRegistry resource server."
            ),
        )
    ),
]


@dataclass(frozen=True)
class GlobusPrincipal:
    """The Globus identity and authorization granted to one API request."""

    subject: str
    username: str | None
    name: str | None
    email: str | None
    scopes: frozenset[str]
    identities: frozenset[str]


def get_globus_auth_client(settings: GlobusAuthSettings | None = None):
    """Build the confidential client used exclusively for token introspection."""
    settings = settings or GlobusAuthSettings()
    if (
        settings.globus_auth_client_id is None
        or settings.globus_auth_client_secret is None
    ):
        raise RuntimeError(
            "Globus Auth is not configured: set DATAREGISTRY_API_GLOBUS_AUTH_CLIENT_ID "
            "and DATAREGISTRY_API_GLOBUS_AUTH_CLIENT_SECRET."
        )
    return globus_sdk.ConfidentialAppAuthClient(
        settings.globus_auth_client_id,
        settings.globus_auth_client_secret.get_secret_value(),
    )


def _response_data(response: Any) -> dict[str, Any]:
    """Return SDK response data while keeping the client easy to fake in tests."""
    if isinstance(response, dict):
        return response
    return response.data


def _configured_settings(settings: GlobusAuthSettings) -> None:
    missing = [
        name
        for name, value in (
            ("DATAREGISTRY_API_GLOBUS_AUTH_EXPECTED_AUDIENCE", settings.globus_auth_expected_audience),
            ("DATAREGISTRY_API_GLOBUS_AUTH_REQUIRED_SCOPE", settings.globus_auth_required_scope),
        )
        if value is None
    ]
    if missing:
        raise RuntimeError(f"Globus Auth is not configured: set {', '.join(missing)}.")


def _principal_from_token_metadata(
    metadata: dict[str, Any], settings: GlobusAuthSettings
) -> GlobusPrincipal:
    """Validate Globus introspection claims and normalize them for the API."""
    _configured_settings(settings)
    if not metadata.get("active"):
        raise AuthenticationError("The bearer token is invalid or expired.")
    if metadata.get("token_type", "Bearer").lower() != "bearer":
        raise AuthenticationError("The supplied credential is not a bearer token.")
    if metadata.get("iss", "").rstrip("/") != settings.globus_auth_issuer.rstrip("/"):
        raise AuthenticationError("The bearer token has an untrusted issuer.")

    audience = set(metadata.get("aud") or ())
    if settings.globus_auth_expected_audience not in audience:
        raise AuthenticationError("The bearer token is not intended for this API.")

    scopes = frozenset((metadata.get("scope") or "").split())
    if settings.globus_auth_required_scope not in scopes:
        raise AuthorizationError("The bearer token lacks the required API scope.")

    subject = metadata.get("sub")
    if not isinstance(subject, str) or not subject:
        raise AuthenticationError("The bearer token has no subject identity.")
    identities = frozenset(metadata.get("identity_set") or (subject,))
    return GlobusPrincipal(
        subject=subject,
        username=metadata.get("username"),
        name=metadata.get("name"),
        email=metadata.get("email"),
        scopes=scopes,
        identities=identities,
    )


def require_globus_principal(credentials: BearerCredentials) -> GlobusPrincipal:
    """Require a valid Globus token with the API's single read scope."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationError("A Globus bearer token is required.")

    settings = GlobusAuthSettings()
    client = get_globus_auth_client(settings)
    response = client.oauth2_token_introspect(credentials.credentials, include="identity_set")
    return _principal_from_token_metadata(_response_data(response), settings)

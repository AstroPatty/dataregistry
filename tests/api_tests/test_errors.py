"""Unit tests for centralized API exception translation."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from dataregistry.exceptions import (
    DataRegistryColumnSpec,
    DataRegistryException,
    DataRegistryNoColumn,
)
from dataregistry_api.errors import register_error_handlers


def _raising_route(error):
    def route():
        raise error

    return route


def _response_for(error):
    app = FastAPI()
    register_error_handlers(app)
    app.add_api_route("/error", _raising_route(error))
    return TestClient(app).get("/error")


def test_core_unknown_column_uses_api_error_envelope():
    response = _response_for(DataRegistryNoColumn("dataset.missing"))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "UNKNOWN_COLUMN"


def test_core_ambiguous_column_uses_api_error_envelope():
    response = _response_for(DataRegistryColumnSpec("description"))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "AMBIGUOUS_COLUMN"


def test_strip_table_names_error_uses_api_error_envelope():
    response = _response_for(
        DataRegistryException("Can only strip out table names for single table queries")
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"

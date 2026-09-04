"""Verify the test database really looks like the production database.

If these fail, the fixtures are not building a production-shaped schema and
nothing else in the API suite can be trusted.
"""

import pytest
from sqlalchemy import text

from dataregistry.schema import load_schema
from dataregistry.schema.schema_version import (
    _DB_VERSION_MAJOR,
    _DB_VERSION_MINOR,
)

from .conftest import PRODUCTION_SCHEMA, WORKING_SCHEMA

EXPECTED_TABLES = sorted(load_schema()["tables"].keys())


@pytest.mark.parametrize("schema", [WORKING_SCHEMA, PRODUCTION_SCHEMA])
def test_schema_exists(connection, schema):
    result = connection.execute(
        text(
            "SELECT 1 FROM information_schema.schemata WHERE schema_name = :schema"
        ),
        {"schema": schema},
    ).scalar_one_or_none()

    assert result == 1, f"schema {schema} was not created"


@pytest.mark.parametrize("schema", [WORKING_SCHEMA, PRODUCTION_SCHEMA])
def test_all_production_tables_exist(inspector, schema):
    """Every table declared in ``schema.yaml`` is present in both schemas."""
    assert sorted(inspector.get_table_names(schema=schema)) == EXPECTED_TABLES


@pytest.mark.parametrize("table", EXPECTED_TABLES)
def test_table_columns_match_the_schema_definition(inspector, table):
    """Column names come from the same YAML production is built from."""
    expected = set(load_schema()["tables"][table]["column_definitions"].keys())
    actual = {
        column["name"]
        for column in inspector.get_columns(table, schema=WORKING_SCHEMA)
    }

    assert expected <= actual, f"{table} is missing columns {expected - actual}"


def test_provenance_row_records_the_current_db_version(connection):
    """The creation script writes provenance exactly as it does in production."""
    row = connection.execute(
        text(
            f"SELECT db_version_major, db_version_minor, update_method, "
            f"associated_production FROM {WORKING_SCHEMA}.provenance "
            f"ORDER BY provenance_id DESC LIMIT 1"
        )
    ).one()

    assert row.db_version_major == _DB_VERSION_MAJOR
    assert row.db_version_minor == _DB_VERSION_MINOR
    assert row.update_method == "CREATE"
    assert row.associated_production == PRODUCTION_SCHEMA


def test_preset_keywords_are_populated(connection):
    """The working schema is seeded with the system keywords, as in production."""
    from dataregistry.schema import load_preset_keywords

    expected = set(load_preset_keywords()["dataset"].keys())
    actual = {
        row.keyword
        for row in connection.execute(
            text(f"SELECT keyword FROM {WORKING_SCHEMA}.keyword WHERE system")
        )
    }

    assert expected <= actual


def test_working_schema_links_to_production(inspector):
    """The dependency table's foreign key crosses into the production schema."""
    foreign_keys = inspector.get_foreign_keys("dependency", schema=WORKING_SCHEMA)
    referred_schemas = {fk["referred_schema"] for fk in foreign_keys}

    assert PRODUCTION_SCHEMA in referred_schemas


def test_search_path_resolves_unqualified_names(connection):
    """Unqualified queries hit the working schema, as the API expects."""
    connection.execute(text("SELECT * FROM dataset LIMIT 1"))


def test_writes_are_rolled_back_between_tests(connection):
    """Guard the isolation contract the rest of the suite relies on.

    NOT NULL is enforced here exactly as it is in production, so the canary
    row has to be complete.
    """
    connection.execute(
        text(
            f"INSERT INTO {WORKING_SCHEMA}.keyword "
            f"(keyword, system, active, creator_uid, creation_date) "
            f"VALUES ('api-test-isolation-canary', false, true, "
            f"'api-tests', now())"
        )
    )
    count = connection.execute(
        text(
            f"SELECT count(*) FROM {WORKING_SCHEMA}.keyword "
            f"WHERE keyword = 'api-test-isolation-canary'"
        )
    ).scalar_one()

    assert count == 1


def test_previous_test_left_nothing_behind(connection):
    """Runs after the canary insert above and must not see it."""
    count = connection.execute(
        text(
            f"SELECT count(*) FROM {WORKING_SCHEMA}.keyword "
            f"WHERE keyword = 'api-test-isolation-canary'"
        )
    ).scalar_one()

    assert count == 0

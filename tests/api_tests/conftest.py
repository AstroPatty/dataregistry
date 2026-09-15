"""Test infrastructure for the ``dataregistry_api`` FastAPI server.

These tests run against a *real* PostgreSQL instance whose schemas are built
by the same script used in production and CI,
``scripts/create_registry_schema.py``. Nothing about the table layout is
mocked or hand-rolled: if the production schema changes, these tests see the
change for free.

Getting a database
------------------
The suite does not start PostgreSQL for you. Point it at one with the same
``DATAREGISTRY_API_*`` environment variables the server itself uses::

    DATAREGISTRY_API_DATABASE_HOST=localhost
    DATAREGISTRY_API_DATABASE_PORT=5432
    DATAREGISTRY_API_DATABASE_NAME=desc_data_registry
    DATAREGISTRY_API_DATABASE_USERNAME=postgres
    DATAREGISTRY_API_DATABASE_PASSWORD=postgres

The defaults above match both ``dev/docker-compose.yml`` and the postgres
service container used by the CI workflow, so locally this is usually enough::

    ./dev/dev-db.sh up --no-seed
    pytest -v tests/api_tests

Skipping
--------
If no database is reachable the whole suite is skipped rather than failed,
unless ``DATAREGISTRY_API_TEST_REQUIRE_DB=1`` is set (CI sets it, so a broken
database is a hard error there).

Schemas
-------
A dedicated working/production schema pair is created for the API tests
(``lsst_desc_api_test_working`` / ``lsst_desc_api_test_production``) so runs
never collide with the end-to-end suite or with seeded dev data. They are
dropped and recreated once per session.

Isolation
---------
Schema creation happens once per session; each test then runs inside a
transaction that is rolled back on teardown, so tests never see each other's
writes.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.exc import OperationalError

from dataregistry_api import database
from dataregistry_api.app import APP
from dataregistry_api.config import DatabaseSettings, get_database_connection_url

REPO_ROOT = Path(__file__).resolve().parents[2]
CREATE_SCHEMA_SCRIPT = REPO_ROOT / "scripts" / "create_registry_schema.py"

#: Set in ``tests/api_tests/__init__.py``, which also exports it to the server
#: via ``DATAREGISTRY_API_NAMESPACE`` before the application is imported.
from . import TEST_NAMESPACE

WORKING_SCHEMA = f"{TEST_NAMESPACE}_working"
PRODUCTION_SCHEMA = f"{TEST_NAMESPACE}_production"

#: Defaults matching dev/docker-compose.yml and the CI postgres service.
_ENV_DEFAULTS = {
    "DATAREGISTRY_API_DATABASE_HOST": "localhost",
    "DATAREGISTRY_API_DATABASE_PORT": "5432",
    "DATAREGISTRY_API_DATABASE_NAME": "desc_data_registry",
    "DATAREGISTRY_API_DATABASE_USERNAME": "postgres",
    "DATAREGISTRY_API_DATABASE_PASSWORD": "postgres",
}


def _require_db() -> bool:
    return os.environ.get("DATAREGISTRY_API_TEST_REQUIRE_DB", "") not in ("", "0")


def _unavailable(reason: str):
    """Fail if a database was mandated, otherwise skip the whole session."""
    if _require_db():
        pytest.fail(reason, pytrace=False)
    pytest.skip(reason, allow_module_level=True)


@pytest.fixture(scope="session", autouse=True)
def database_environment() -> DatabaseSettings:
    """Fill in connection defaults and return the resolved settings."""
    for key, value in _ENV_DEFAULTS.items():
        os.environ.setdefault(key, value)
    return DatabaseSettings()


@pytest.fixture(scope="session")
def database_url(database_environment: DatabaseSettings):
    return get_database_connection_url(database_environment)


@pytest.fixture(scope="session")
def registry_config_file(database_url, tmp_path_factory) -> Path:
    """Write the ``sqlalchemy.url`` config file the core library expects.

    ``scripts/create_registry_schema.py`` reaches the database through
    ``dataregistry.db_basic.DbConnection``, which reads a YAML config file
    rather than our environment variables. ``DbConnection`` also refuses to
    read a password-bearing config that is group- or world-readable, hence
    the ``chmod``.
    """
    config_file = tmp_path_factory.mktemp("dataregistry-api") / "config.yaml"
    # `render_as_string` masks the password unless we ask for it.
    url = database_url.render_as_string(hide_password=False)
    config_file.write_text(f"sqlalchemy.url: {url}\n")
    config_file.chmod(0o600)
    return config_file


@pytest.fixture(scope="session")
def registry_database(database_url, registry_config_file: Path) -> None:
    """Build a production-shaped schema pair, once per session.

    Any pre-existing API-test schemas are dropped first so repeated local runs
    start from a clean slate (CI gets a fresh container each time).
    """
    admin_engine = create_engine(database_url, poolclass=None)

    try:
        with admin_engine.begin() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError as exc:
        admin_engine.dispose()
        _unavailable(
            "Could not connect to the test database at "
            f"{database_url.render_as_string()}: {exc}\n"
            "Start one with `./dev/dev-db.sh up --no-seed` or set the "
            "DATAREGISTRY_API_DATABASE_* environment variables."
        )

    try:
        with admin_engine.begin() as conn:
            for schema in (WORKING_SCHEMA, PRODUCTION_SCHEMA):
                conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
    finally:
        admin_engine.dispose()

    _create_schemas(registry_config_file)


def _create_schemas(config_file: Path) -> None:
    """Invoke the production schema-creation script in a subprocess.

    The script is top-level argparse code rather than an importable function,
    and it can only run once per interpreter (it mutates a module-global
    SQLAlchemy ``Base``). Shelling out keeps the schema byte-identical to what
    CI and NERSC produce without refactoring production tooling.
    """
    command = [
        sys.executable,
        str(CREATE_SCHEMA_SCRIPT),
        "--config",
        str(config_file),
        "--schema",
        WORKING_SCHEMA,
        "--production-schema",
        PRODUCTION_SCHEMA,
        "--create-both",
        # The `reg_reader` / `reg_writer` roles do not exist on a stock
        # container; the script warns rather than fails, but relaxing the
        # grants keeps the test schemas usable if they do exist.
        "--no-permission-restrictions",
    ]

    result = subprocess.run(
        command,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Failed to create the data registry test schemas.\n"
            f"command: {' '.join(command)}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )


@pytest.fixture(scope="session")
def engine(database_url, registry_database) -> Engine:
    """A session-scoped engine bound to the freshly built test schemas.

    ``search_path`` is set on every connection so unqualified table names
    resolve to the working schema, with the production schema behind it.
    """
    engine = create_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={"options": f"-csearch_path={WORKING_SCHEMA},{PRODUCTION_SCHEMA}"},
    )
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def application_engine(engine: Engine):
    """Make the application use the test engine instead of building its own.

    ``set_engine`` marks the engine as caller-owned, so the application's
    shutdown hook will not dispose of it out from under the session.
    """
    previous = database.set_engine(engine)
    try:
        yield engine
    finally:
        database.set_engine(previous)


#: Tables the seed corpus writes to, in delete-safe order (children first).
#: `keyword` and `provenance` are deliberately absent: they are populated by
#: `create_registry_schema.py` and the corpus only reads from them.
_SEEDED_TABLES = ("dataset_keyword", "dependency", "dataset", "execution")


def _truncate_seeded_tables(conn) -> None:
    """Remove every seeded row from both schemas and reset the id sequences.

    `RESTART IDENTITY` keeps generated ids stable from test to test, and
    `CASCADE` covers any table that gained a foreign key to these since.
    """
    targets = ", ".join(
        f"{schema}.{table}"
        for schema in (WORKING_SCHEMA, PRODUCTION_SCHEMA)
        for table in _SEEDED_TABLES
    )
    conn.execute(text(f"TRUNCATE {targets} RESTART IDENTITY CASCADE"))
    conn.commit()


@pytest.fixture
def connection(engine: Engine):
    """A committing connection, truncated back to empty after each test.

    Rollback-based isolation is not usable here: the endpoints reach the
    database through an :class:`~sqlalchemy.Engine` and open their own
    connections, so they cannot see writes that are still uncommitted in
    another transaction. The corpus is therefore committed for real and
    cleaned up afterwards.
    """
    with engine.connect() as conn:
        _truncate_seeded_tables(conn)
        try:
            yield conn
        finally:
            _truncate_seeded_tables(conn)


@pytest.fixture
def client(connection):
    """A ``TestClient`` for the application under test.

    Handlers are left to obtain their own connections from the shared test
    engine; because the corpus is committed, they see it. Depending on
    ``connection`` keeps the truncation lifecycle tied to every test that
    talks to the API.
    """
    from fastapi.testclient import TestClient

    with TestClient(APP) as test_client:
        yield test_client


@pytest.fixture
def inspector(connection):
    """SQLAlchemy inspector bound to the test connection."""
    return inspect(connection)


@pytest.fixture
def seeded(connection):
    """Insert and commit the shared query corpus.

    The commit is what makes the rows visible to endpoints, which query over
    their own engine-owned connections. The ``connection`` fixture truncates
    the tables again on teardown.
    """
    from .seed import seed_registry

    registry = seed_registry(connection, WORKING_SCHEMA, PRODUCTION_SCHEMA)
    connection.commit()
    return registry


@pytest.fixture
def query(client):
    """Call ``POST /datasets/query`` and return the raw response.

    The positional argument is the JSON body; keyword arguments become
    query-string parameters. Only ``namespace`` and ``query_mode`` live in the
    query string — pagination, ordering and response shape are body fields, so
    pass those inside ``body``. ``None`` parameters are dropped so callers can
    exercise server-side defaults.

    The body defaults to ``{}`` rather than being omitted: the endpoint now
    requires one, and ``{}`` is the documented way to say "no constraints".
    """

    def call(body: dict | None = None, **parameters):
        params = {k: v for k, v in parameters.items() if v is not None}
        return client.post("/datasets/query", json=body or {}, params=params)

    return call


@pytest.fixture
def rows(query):
    """Call ``/datasets/query``, assert a 200, and return the `records` rows."""

    def call(body: dict | None = None, **parameters):
        response = query(body, **parameters)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["format"] == "records"
        return payload["data"]

    return call

# API server tests

Tests for the `dataregistry_api` FastAPI server. They run against a **real**
PostgreSQL database whose schemas are built by the same script production and
CI use, `scripts/create_registry_schema.py`. No table layout is mocked, so a
change to `src/dataregistry/schema/schema.yaml` is picked up automatically.

## Running them

Install the test dependencies and point the suite at a Postgres instance:

```bash
pip install --group api-test      # or: uv sync --group api-test

# Easiest: reuse the dev container.
./dev/dev-db.sh up --no-seed

pytest -v tests/api_tests
```

The suite defaults to `localhost:5432`, database `desc_data_registry`, user
`postgres`/`postgres` — matching both `dev/docker-compose.yml` and the CI
postgres service. Override with the same variables the server itself reads:

| Variable                             | Default              |
| ------------------------------------ | -------------------- |
| `DATAREGISTRY_API_DATABASE_HOST`     | `localhost`          |
| `DATAREGISTRY_API_DATABASE_PORT`     | `5432`               |
| `DATAREGISTRY_API_DATABASE_NAME`     | `desc_data_registry` |
| `DATAREGISTRY_API_DATABASE_USERNAME` | `postgres`           |
| `DATAREGISTRY_API_DATABASE_PASSWORD` | `postgres`           |

If no database is reachable the suite **skips** rather than fails. Set
`DATAREGISTRY_API_TEST_REQUIRE_DB=1` to make an unreachable database a hard
error; CI sets this so a broken database can never pass silently.

## How it works

- **Schemas.** A session fixture drops and recreates the pair
  `lsst_desc_api_test_working` / `lsst_desc_api_test_production` by shelling
  out to `scripts/create_registry_schema.py --create-both`. The dedicated
  namespace means API test runs never collide with the end-to-end suite or
  with seeded dev data, and you don't need to reset the container between runs.
- **Isolation.** Schema creation happens once per session. Each test then runs
  inside a transaction that is rolled back on teardown, so tests never observe
  each other's writes. `test_schema.py` has a canary pair of tests that guard
  this contract.
- **The app.** `dataregistry_api.database` builds its engine lazily, so
  importing the app never requires a database. The fixtures install the test
  engine via `set_engine()` and override the `get_database_connection`
  dependency, so request handlers share the test's transaction — anything an
  endpoint writes is visible to the test and discarded afterwards.

## Fixtures

| Fixture      | Scope   | What you get                                             |
| ------------ | ------- | -------------------------------------------------------- |
| `engine`     | session | SQLAlchemy engine with `search_path` set to the test schemas |
| `connection` | function| Connection inside a transaction rolled back after the test |
| `client`     | function| `TestClient` whose handlers share that transaction        |
| `inspector`  | function| SQLAlchemy `Inspector` bound to the test connection       |

## Adding tests for a new endpoint

Request the `client` fixture and call it. To set up rows first, use
`connection` — the endpoint will see them, and both are rolled back together:

```python
def test_get_dataset(client, connection):
    connection.execute(text("INSERT INTO dataset (...) VALUES (...)"))

    response = client.get("/datasets/1")

    assert response.status_code == 200
```

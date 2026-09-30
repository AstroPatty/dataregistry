# Tier-1 Read API Implementation Plan

## Purpose

Deliver the smallest useful, authenticated, read-only HTTP API for the
DataRegistry. It must allow an external user to discover datasets and construct
valid queries without direct database access.

The Tier-1 API comprises:

- `POST /datasets/query`
- `GET /datasets/{dataset_id}`
- `GET /schema/tables`
- `GET /schema/columns`
- `GET /schema/operators`

Dataset paths, keywords, aliases, aggregations, Globus transfers, writes, and
authentication beyond the API's initial Globus Auth read scope are deliberately deferred.

## Architecture decision

The API server must use the existing `dataregistry` library for registry query
semantics. It must not become a second implementation of SQL construction,
schema reflection, joins, or filter interpretation.

```text
HTTP client
    |
    v
dataregistry_api
    |  HTTP validation, error translation, JSON response shaping
    v
dataregistry
    |  column resolution, joins, filters, ordering, pagination, counting
    v
SQLAlchemy Engine / PostgreSQL
```

The API will continue to inject an SQLAlchemy `Engine` into
`dataregistry.db_basic.DbConnection`, which already supports the `engine=`
argument. `Query` manages its short-lived database connections as it does for
existing direct-library clients. Do not change API dependencies to inject a
SQLAlchemy `Connection` merely for query execution.

This keeps one source of truth while direct-library access remains supported.
In the longer term, users will migrate to a new client library that calls the
HTTP API, while the server may continue to use `dataregistry` internally.

## Non-goals

- Authentication mechanisms other than Globus Auth bearer-token validation, or
  authorization beyond the initial read scope. Globus Auth manages the identity
  requirements for that scope; future server-side policy checks may extend it.
- Dataset registration, modification, replacement, deletion, or keyword
  creation.
- Globus transfer initiation or status polling.
- Replacing the existing Python library with an HTTP client.
- Adding API/Pydantic/HTTP concepts to the core library.
- Using `dataregistry_api.catalog` or `dataregistry_api.query_builder` as a
  production query path. Those modules duplicate library query behavior and
  should remain unused by Tier 1 (or be removed in a follow-up cleanup).

## Phase 1: Extend the library query interface

The API requires server-side ordering, pagination, and matching-row counts.
Implement these as backwards-compatible enhancements to
`src/dataregistry/query.py`, so direct-library and API callers share the same
semantics.

### 1. Add ordering and pagination to `Query.find_datasets`

Extend the public method signature without changing existing defaults:

```python
def find_datasets(
    self,
    property_names=None,
    filters=[],
    return_format="property_dict",
    strip_table_names=False,
    schema_mode=None,
    order_by=None,
    limit=None,
    offset=0,
):
```

Required behavior:

- `order_by=None`, `limit=None`, and `offset=0` retain current direct-library
  behavior.
- Accept a small, library-native ordered representation, such as a list of
  `(column_name, direction)` pairs. The API converts its structured `SortKey`
  models into this representation.
- Resolve sort columns through the existing property-name normalization logic.
  A sort column need not be selected, just as a filter column need not be
  selected.
- Validate sort directions (`asc` and `desc`) in the library. API-level models
  continue to validate the public request format.
- Add `ORDER BY`, `LIMIT`, and `OFFSET` to the SQLAlchemy statements before
  execution. Do not page a fully materialized pandas DataFrame.
- Preserve existing query-mode behavior. In `query_mode="both"`, the public
  result sequence is working-schema rows followed by production-schema rows.
  Apply `offset` and `limit` to this combined sequence, not independently to
  both schema queries.
- Preserve all existing filter, wildcard, join, ambiguous-column, and
  `strip_table_names` behavior.

Refactor private query-building helpers where necessary so dataset queries and
counts share the same column resolution, required-table discovery, joins, and
filter rendering. Keep the public change limited to the optional arguments
above.

### 2. Add `Query.count_datasets`

Add a public method:

```python
def count_datasets(self, filters=[], schema_mode=None) -> int:
```

Required behavior:

- Apply the same joins and filters as `find_datasets`.
- Execute SQL `COUNT(*)`; do not fetch all rows merely to obtain a count.
- In `query_mode="both"`, return the sum of working and production counts.
- Count query-result rows, not distinct dataset IDs. This must match
  `find_datasets` result cardinality, including one-to-many keyword or
  dependency joins.
- Preserve existing defaults and direct-library behavior.

The method may need an optional internal column/table context because a count
with filters on joined tables must retain the relevant joins. Design the helper
so the API can request a count matching its selected/filter/sort query without
reimplementing SQL logic.

### 3. Library tests

Add direct-library tests under `tests/end_to_end_tests/` for:

- ordering by selected and unselected columns;
- multiple sort keys and both directions;
- `limit` and `offset`;
- no limit preserving full results;
- working, production, and both query modes;
- a page crossing the working/production boundary;
- count behavior with filters and joins, including keyword-induced duplicate
  result rows;
- invalid sort direction and invalid sort column errors.

Existing tests must continue to pass without modification except where they are
expanded to cover the new optional behavior.

## Phase 2: Complete `POST /datasets/query`

Keep the route as an adapter over `dataregistry.Query`.

### 1. Handler implementation

In `src/dataregistry_api/handlers/dataset.py`:

1. Construct `DbConnection(namespace=..., engine=..., query_mode=...)` from
   the injected API engine.
2. Construct `Query(connection, root_dir="/")`. The root directory is a
   placeholder because Tier 1 does not resolve dataset paths.
3. Convert API `Filter` objects with `as_library_filter()`.
4. Convert API `SortKey` values to the new library `order_by` representation.
5. Call `Query.count_datasets(...)` to populate `total_count`.
6. Call `Query.find_datasets(..., return_format="dataframe", order_by=...,
   limit=..., offset=...)` to obtain the requested page.
7. Convert the DataFrame to the requested JSON response shape.

The API must not use `dataregistry_api.catalog`,
`dataregistry_api.query_builder`, or direct SQLAlchemy statement execution for
the dataset query.

### 2. API response shaping

Keep these HTTP-specific transformations in `dataregistry_api`:

- `records` is the canonical JSON wire format.
- Preserve fully-qualified result keys by default, such as `dataset.name`.
- When `strip_table_names=true`, strip prefixes only for a single-table query;
  reject multi-table stripping with a client error.
- If `dataset.status` is selected (including the default all-dataset-columns
  query), replace it with:
  - `dataset.status_raw`: original integer bitmask;
  - `dataset.status`: object with `valid`, `deleted`, `archived`, and
    `replaced` booleans.
  Use `dataregistry.registrar.dataset_util.get_dataset_status` and
  `VALID_STATUS_BITS`; do not duplicate status-bit definitions.
- Apply the same prefix stripping to generated `status` and `status_raw` keys.
- Serialize `datetime` and `date` values as ISO-8601 strings; retain JSON null
  values and numeric precision.
- Return `page_count`, `total_count`, applied `limit`, and applied `offset`
  through `DatasetQueryResponse`.

### 3. Error translation

Map exceptions raised by the core library to the documented API error envelope
in `dataregistry_api.errors`:

- unknown columns -> `UNKNOWN_COLUMN`, HTTP 422;
- ambiguous bare columns -> `AMBIGUOUS_COLUMN`, HTTP 422;
- invalid query/filter/sort inputs -> `INVALID_FILTER` or `INVALID_REQUEST`,
  HTTP 422;
- invalid namespaces -> `UNKNOWN_NAMESPACE`, HTTP 404.

Avoid changing library exceptions solely to satisfy HTTP. The API owns the
translation from Python exceptions to stable HTTP status codes and error bodies.

### 4. API tests

Finish and make passing `tests/api_tests/test_datasets_query.py`. It already
defines the expected contract for filters, joins, wildcard handling, schema
modes, pagination, ordering, response formats, status expansion,
serialization, and non-writing behavior.

Repair or remove incomplete handler-only code, including the unused
`_fetch_page` helper and undefined `_count`/`_execute` references, rather than
leaving a competing SQL execution path.

## Phase 3: Add single-dataset lookup

Expose `GET /datasets/{dataset_id}` as a convenience endpoint.

- Add the route to `src/dataregistry_api/routes/dataset.py`.
- Accept `namespace` and one schema selector (`working` or `production`);
  reject `both` for this endpoint because a dataset ID can exist in both
  schemas.
- Implement it through the same library-backed query adapter used by
  `/datasets/query`, with an equality filter on `dataset.dataset_id`, all
  dataset columns, and `limit=1`.
- Return the normal API dataset representation, including expanded status.
- Add a `DatasetNotFoundError` (or equivalent API-boundary translation) for an
  empty result: HTTP 404 with the standard error envelope.

Add `tests/api_tests/test_dataset_get.py` covering successful lookup, expanded
status, unknown IDs, working vs. production selection, and rejection of
ambiguous `both` addressing.

## Phase 4: Add schema introspection

Create `src/dataregistry_api/routes/schema.py` and corresponding handler/model
code. Register the router in `routes/__init__.py`.

### `GET /schema/tables`

- Use `Query.get_all_tables()`.
- Return `{ "tables": [...] }` in stable sorted order.

### `GET /schema/columns`

- Use `Query.get_all_columns()`.
- Support the documented `table`, `include_table`, and `include_schema`
  parameters.
- Validate a requested table and return a documented client error rather than a
  server error for an unknown table.
- Keep the endpoint read-only and library-backed.

### `GET /schema/operators`

This endpoint is static discoverability metadata, not a database query:

- derive operators from `dataregistry.query._colops`;
- use `dataregistry.query.ILIKE_ALLOWED` for wildcard-eligible columns;
- expose wildcard character `*`;
- expose the operator subset valid for non-orderable columns.

Create `tests/api_tests/test_schema.py` that asserts these values against the
library constants, preventing drift.

## Phase 5: Align the published contract

Update `openapi.yaml` and the FastAPI metadata so the published API describes
only endpoints that are actually deployed:

- retain the five Tier-1 endpoints;
- remove the global bearer-auth requirement for this unauthenticated phase;
- retain the error envelope and response models;
- document default `working` query mode, pagination bounds, `both`-mode result
  ordering, and status expansion;
- remove or explicitly mark as planned-but-unimplemented aliases, keywords,
  aggregates, paths, generic entity lookup, transfers, and writes.

Ensure FastAPI's generated OpenAPI schema and `openapi.yaml` do not contradict
each other. Add narrow tests for important defaults and response models.

## Verification and acceptance criteria

Run the full API suite against PostgreSQL:

```bash
./dev/dev-db.sh up --no-seed
pytest -v tests/api_tests
```

Also run affected direct-library tests:

```bash
pytest -v tests/end_to_end_tests/test_query.py tests/end_to_end_tests/test_production_schema.py
```

Tier 1 is complete when:

1. All five endpoints are registered and documented.
2. Dataset query behavior is performed through `dataregistry.Query`, including
   filtering, joins, ordering, pagination, and counting.
3. The core library additions are backwards compatible for existing direct
   clients.
4. No Tier-1 endpoint performs writes.
5. API errors use the documented envelope and do not expose library exceptions
   as HTTP 500 responses for invalid client input.
6. The implementation, tests, and published OpenAPI contract agree.

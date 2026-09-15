"""Handler for ``POST /datasets/query``."""

from __future__ import annotations

from sqlalchemy import Connection, Engine

from dataregistry.db_basic import DbConnection
from dataregistry.query import Query
from dataregistry_api.catalog import ROOT_TABLE
from dataregistry_api.models import (
    DatasetQueryParameters,
    DatasetQueryRequest,
    DatasetQueryResponse,
)

#: Result key holding the raw `status` bitmask, alongside the expanded form.
STATUS_COLUMN = f"{ROOT_TABLE}.status"
STATUS_RAW_COLUMN = f"{ROOT_TABLE}.status_raw"


def get_query(engine: Engine, parameters: DatasetQueryParameters) -> Query:
    connection = DbConnection(
        namespace=parameters.namespace,
        engine=engine,
        query_mode=parameters.query_mode,
    )
    return Query(connection, "/")  # placeholder path


def find_datasets(
    engine: Engine,
    parameters: DatasetQueryParameters,
    request: DatasetQueryRequest | None = None,
) -> DatasetQueryResponse:
    """Return the datasets matching a query.

    Columns named in ``property_names`` are selected from the ``dataset``
    table and any table reachable from it; the joins are inferred from the
    columns the request mentions, including those it only filters or sorts on.
    Omitting ``property_names`` selects every ``dataset`` column.

    When ``query_mode`` is ``both`` the working and production schemas are
    queried in turn and their rows concatenated, working first. Pagination is
    applied to that combined sequence so a page never straddles the two
    schemas inconsistently.

    Parameters
    ----------
    db_connection : Connection
        Open connection; the query runs inside the caller's transaction.
    parameters : DatasetQueryParameters
        Addressing: which namespace and which schema(s) to read.
    request : DatasetQueryRequest, optional
        The query itself. ``None`` is treated as the default request, i.e.
        the first page of all ``dataset`` columns, unfiltered.

    Returns
    -------
    DatasetQueryResponse
        Rows in the requested format, with the applied pagination echoed back
        and the total size of the match set.

    Raises
    ------
    QueryError
        If a column cannot be resolved, or a filter cannot be applied to its
        column. Surfaces as a 4xx with the documented error envelope.
    """
    dreg_query = get_query(engine, parameters)
    filters = [f.as_library_filter() for f in request.filters]
    result = dreg_query.find_datasets(
        property_names=request.property_names,
        filters=filters,
        return_format="dataframe",
        strip_table_names=request.strip_table_names,
    )

    return DatasetQueryResponse.from_records(result.to_dict(orient="records"))


def _fetch_page(
    connection: Connection, statements: list, limit: int, offset: int
) -> list:
    """Fetch one page across the statements, treating them as concatenated.

    With a single schema this is a plain LIMIT/OFFSET. With ``both`` the page
    may span the two schemas, so each is asked only for the slice that falls
    inside the window.
    """
    rows: list = []
    remaining = limit
    to_skip = offset

    for statement in statements:
        if remaining <= 0:
            break
        matched = _count(connection, statement)
        if to_skip >= matched:
            to_skip -= matched
            continue
        page = statement.limit(remaining).offset(to_skip)
        fetched = _execute(connection, page).fetchall()
        rows.extend(fetched)
        remaining -= len(fetched)
        to_skip = 0

    return rows

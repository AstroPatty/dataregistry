"""Handler for ``POST /datasets/query``."""

from __future__ import annotations

from sqlalchemy import Engine

from dataregistry.db_basic import DbConnection
from dataregistry.query import Query
from dataregistry_api.models import (
    DatasetQueryParameters,
    DatasetQueryRequest,
    DatasetQueryResponse,
    ReturnFormat,
)

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
    request = request or DatasetQueryRequest()
    dreg_query = get_query(engine, parameters)
    filters = [f.as_library_filter() for f in request.filters]
    order_by = [(sort.column, sort.direction.value) for sort in request.order_by]

    # A count must use the same selection and ordering context as the page:
    # either can require joins that alter result-row cardinality.
    total_count = dreg_query.count_datasets(
        filters=filters,
        _property_names=request.property_names,
        _order_by=order_by,
    )
    result = dreg_query.find_datasets(
        property_names=request.property_names,
        filters=filters,
        return_format="dataframe",
        strip_table_names=request.strip_table_names,
        order_by=order_by,
        limit=request.limit,
        offset=request.offset,
    )

    if request.return_format is ReturnFormat.PROPERTY_DICT:
        return DatasetQueryResponse.from_property_dict(
            result.to_dict(orient="list"),
            total_count=total_count,
            limit=request.limit,
            offset=request.offset,
        )

    return DatasetQueryResponse.from_records(
        result.to_dict(orient="records"),
        total_count=total_count,
        limit=request.limit,
        offset=request.offset,
    )

"""Handler for ``POST /datasets/query``."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any, Iterable

from sqlalchemy import Connection, func, select
from sqlalchemy.exc import DBAPIError

from dataregistry.registrar.dataset_util import VALID_STATUS_BITS
from dataregistry_api.catalog import ROOT_TABLE, get_catalog, schemas_for_mode
from dataregistry_api.errors import QueryError
from dataregistry_api.models import (
    DatasetQueryParameters,
    DatasetQueryRequest,
    DatasetQueryResponse,
    PropertyDictData,
    RecordsData,
    ReturnFormat,
)
from dataregistry_api.query_builder import (
    ResolvedFilter,
    ResolvedSortKey,
    build_statement,
    label_for,
)

#: Result key holding the raw `status` bitmask, alongside the expanded form.
STATUS_COLUMN = f"{ROOT_TABLE}.status"
STATUS_RAW_COLUMN = f"{ROOT_TABLE}.status_raw"


def find_datasets(
    db_connection: Connection,
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
    if request is None:
        request = DatasetQueryRequest()

    catalog = get_catalog(db_connection, parameters.namespace)

    select_columns = _select_columns(catalog, request)
    filters = _resolve_filters(catalog, request)
    order_by = _resolve_order_by(catalog, request)

    schemas = schemas_for_mode(parameters.namespace, parameters.query_mode)
    statements = [
        build_statement(catalog, schema, select_columns, filters, order_by)
        for schema in schemas
    ]

    total_count = sum(_count(db_connection, stmt) for stmt in statements)
    rows = _fetch_page(db_connection, statements, request.limit, request.offset)

    records = [_row_to_record(row, select_columns, request) for row in rows]

    if request.return_format is ReturnFormat.PROPERTY_DICT:
        return DatasetQueryResponse.from_property_dict(
            _to_property_dict(records, _result_keys(select_columns, request)),
            total_count=total_count,
            limit=request.limit,
            offset=request.offset,
        )

    return DatasetQueryResponse.from_records(
        records,
        total_count=total_count,
        limit=request.limit,
        offset=request.offset,
    )


def _select_columns(catalog, request: DatasetQueryRequest) -> list[str]:
    """Canonical names of the projected columns.

    An omitted ``property_names`` means every column of ``dataset`` and
    nothing from any other table, so no join is induced.
    """
    if request.property_names is None:
        return [f"{ROOT_TABLE}.{name}" for name in catalog.columns_by_table[ROOT_TABLE]]
    return catalog.resolve(request.property_names)


def _resolve_filters(catalog, request: DatasetQueryRequest) -> list[ResolvedFilter]:
    names = catalog.resolve([f.property_name for f in request.filters])
    return [
        ResolvedFilter.of(filter_, name)
        for filter_, name in zip(request.filters, names)
    ]


def _resolve_order_by(catalog, request: DatasetQueryRequest) -> list[ResolvedSortKey]:
    names = catalog.resolve([key.column for key in request.order_by])
    return [ResolvedSortKey.of(key, name) for key, name in zip(request.order_by, names)]


def _count(connection: Connection, statement) -> int:
    """Number of rows the statement matches, ignoring limit/offset."""
    # `order_by` is irrelevant to a count and some backends reject it inside a
    # subquery without a matching projection, so drop it.
    counted = statement.order_by(None).subquery()
    return connection.execute(select(func.count()).select_from(counted)).scalar_one()


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


def _execute(connection: Connection, statement):
    try:
        return connection.execute(statement)
    except DBAPIError as exc:
        # A value the database itself rejects (a malformed cast, say) is the
        # client's fault, not a server fault; report it as such.
        raise QueryError(
            "The database rejected the query.",
            str(exc.orig) if exc.orig is not None else str(exc),
        ) from exc


def _result_keys(select_columns: list[str], request: DatasetQueryRequest) -> list[str]:
    """Keys each result row carries, in order.

    Selecting `dataset.status` yields two keys: the expanded booleans under
    `status` and the original bitmask under `status_raw`.
    """
    keys: list[str] = []
    for name in select_columns:
        key = label_for(name)
        keys.append(_strip(key, request))
        if name == STATUS_COLUMN:
            keys.append(_strip(STATUS_RAW_COLUMN, request))
    return keys


def _strip(key: str, request: DatasetQueryRequest) -> str:
    return key.split(".", 1)[1] if request.strip_table_names else key


def _row_to_record(
    row, select_columns: list[str], request: DatasetQueryRequest
) -> dict[str, Any]:
    record: dict[str, Any] = {}
    for name, value in zip(select_columns, row):
        key = label_for(name)
        if name == STATUS_COLUMN:
            record[_strip(key, request)] = _expand_status(value)
            record[_strip(STATUS_RAW_COLUMN, request)] = value
        else:
            record[_strip(key, request)] = _serialize(value)
    return record


def _expand_status(value: Any) -> dict[str, bool] | None:
    """Expand the `status` bitmask into the documented booleans."""
    if value is None:
        return None
    return {name: bool(value & (1 << bit)) for name, bit in VALID_STATUS_BITS.items()}


def _serialize(value: Any) -> Any:
    """Convert a database value into something JSON-representable."""
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (bytes, memoryview)):
        return bytes(value).hex()
    return value


def _to_property_dict(records: RecordsData, keys: Iterable[str]) -> PropertyDictData:
    """Pivot records into column-oriented form.

    The keys are taken from the projection rather than the rows so that an
    empty result still describes its shape.
    """
    return {key: [record[key] for record in records] for key in keys}

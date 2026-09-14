"""Translation of a :class:`DatasetQueryRequest` into a SQLAlchemy statement.

Kept separate from the handler so the interesting logic — join inference,
filter rendering, wildcard escaping — is testable without a request cycle.

The semantics deliberately track ``dataregistry.query.Query``: the same
operator set, the same ``*`` wildcard with ``%``/``_`` escaped, the same
allowlist for wildcard matching, and the same inner joins inferred from the
tables the requested columns touch.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Column, Select, Table, select
from sqlalchemy.sql.elements import ColumnElement

from dataregistry.query import ILIKE_ALLOWED, _colops, is_orderable_type
from dataregistry_api.catalog import ROOT_TABLE, Catalog
from dataregistry_api.errors import InvalidFilterError, QueryError
from dataregistry_api.models import (
    WILDCARD_CHAR,
    Filter,
    FilterValue,
    Operator,
    SortDirection,
    SortKey,
)


@dataclass(frozen=True)
class ResolvedFilter:
    """A request filter whose column has been resolved to ``table.column``."""

    canonical_name: str
    op: Operator
    value: FilterValue

    @classmethod
    def of(cls, filter_: Filter, canonical_name: str) -> "ResolvedFilter":
        return cls(canonical_name, filter_.op, filter_.value)


@dataclass(frozen=True)
class ResolvedSortKey:
    """A sort key whose column has been resolved to ``table.column``."""

    canonical_name: str
    direction: SortDirection

    @classmethod
    def of(cls, key: SortKey, canonical_name: str) -> "ResolvedSortKey":
        return cls(canonical_name, key.direction)

#: Tables whose join to `dataset` is not a plain foreign key and so is built
#: explicitly below.
_KEYWORD_TABLE = "keyword"
_DATASET_KEYWORD_TABLE = "dataset_keyword"
_DEPENDENCY_TABLE = "dependency"


def label_for(canonical_name: str) -> str:
    """Result key for a canonical ``table.column`` name.

    Results are fully qualified so that ``dataset.name`` and ``execution.name``
    can coexist in one row.
    """
    return canonical_name


def build_statement(
    catalog: Catalog,
    schema: str,
    select_columns: list[str],
    filters: list[ResolvedFilter],
    order_by: list[ResolvedSortKey],
) -> Select:
    """Build the SELECT for one schema.

    ``select_columns`` are canonical ``table.column`` names. Filters and sort
    keys carry their own resolved columns, which contribute tables to the join
    even when they are not part of the projection.
    """
    tables_required = _tables_for(
        select_columns
        + [f.canonical_name for f in filters]
        + [k.canonical_name for k in order_by]
    )

    columns = [_column(catalog, schema, name) for name in select_columns]
    statement = select(
        *[column.label(label_for(name)) for column, name in zip(columns, select_columns)]
    )
    statement = statement.select_from(_join(catalog, schema, tables_required))

    for filter_ in filters:
        statement = _apply_filter(catalog, schema, statement, filter_)

    for key in order_by:
        statement = statement.order_by(_order_term(catalog, schema, key))

    return statement


def _tables_for(canonical_names: list[str]) -> list[str]:
    """Tables referenced by a set of canonical names, `dataset` always first."""
    tables = {name.split(".", 1)[0] for name in canonical_names}
    tables.add(ROOT_TABLE)
    return [ROOT_TABLE] + sorted(tables - {ROOT_TABLE})


def _column(catalog: Catalog, schema: str, canonical_name: str) -> Column:
    table_name, column_name = canonical_name.split(".", 1)
    return catalog.table(schema, table_name).columns[column_name]


def _join(catalog: Catalog, schema: str, tables_required: list[str]):
    """Infer the join across the tables a query touches.

    Joins are inner, matching the library: a dataset with no execution drops
    out of a query that selects execution columns.
    """
    dataset: Table = catalog.table(schema, ROOT_TABLE)
    if tables_required == [ROOT_TABLE]:
        return dataset

    joined = dataset
    for table_name in tables_required:
        # `dataset` is the root; the other two need explicit conditions.
        if table_name in (ROOT_TABLE, _KEYWORD_TABLE, _DEPENDENCY_TABLE):
            continue
        joined = joined.join(catalog.table(schema, table_name))

    # `keyword` is many-to-many, reached through the `dataset_keyword` link.
    if _KEYWORD_TABLE in tables_required:
        joined = joined.join(catalog.table(schema, _DATASET_KEYWORD_TABLE)).join(
            catalog.table(schema, _KEYWORD_TABLE)
        )

    # `dependency` has two foreign keys back to `dataset`; say which one.
    if _DEPENDENCY_TABLE in tables_required:
        dependency = catalog.table(schema, _DEPENDENCY_TABLE)
        joined = joined.join(
            dependency, dependency.columns.input_id == dataset.columns.dataset_id
        )

    return joined


def _order_term(
    catalog: Catalog, schema: str, key: ResolvedSortKey
) -> ColumnElement:
    column = _column(catalog, schema, key.canonical_name)
    return column.desc() if key.direction is SortDirection.DESC else column.asc()


def _apply_filter(
    catalog: Catalog, schema: str, statement: Select, filter_: ResolvedFilter
) -> Select:
    column = _column(catalog, schema, filter_.canonical_name)
    operator = filter_.op

    if operator in (Operator.ILIKE, Operator.LIKE):
        return statement.where(_wildcard_clause(column, filter_))

    if operator in (Operator.LT, Operator.LE, Operator.GT, Operator.GE):
        if not is_orderable_type(column.type):
            raise InvalidFilterError(
                f'Cannot apply "{operator.value}" to {filter_.canonical_name!r}: '
                "the column is not orderable.",
                "Ordering comparisons require a numeric or datetime column.",
            )

    value = _coerce(column, filter_)
    return statement.where(getattr(column, _colops[operator.value])(value))


def _wildcard_clause(column: Column, filter_: ResolvedFilter) -> ColumnElement:
    """Render ``~=`` / ``~==`` as an (I)LIKE with `*` as the only wildcard."""
    if filter_.canonical_name not in ILIKE_ALLOWED:
        raise InvalidFilterError(
            f'Cannot apply "{filter_.op.value}" to {filter_.canonical_name!r}.',
            f"Wildcard matching is allowed on: {', '.join(sorted(ILIKE_ALLOWED))}",
        )
    if not isinstance(filter_.value, str):
        raise InvalidFilterError(
            f'"{filter_.op.value}" requires a string value, got '
            f"{type(filter_.value).__name__}.",
        )

    # SQL's own wildcards are escaped so that only `*` is special, then `*` is
    # translated to SQL's `%`. Order matters: escape first, translate second.
    pattern = (
        filter_.value.replace("%", r"\%")
        .replace("_", r"\_")
        .replace(WILDCARD_CHAR, "%")
    )
    if filter_.op is Operator.ILIKE:
        return column.ilike(pattern)
    return column.like(pattern)


def _coerce(column: Column, filter_: ResolvedFilter) -> Any:
    """Convert a JSON scalar to something the column can be compared against.

    JSON has no date type, so an ISO-8601 string filtered against a datetime
    column is parsed here. A value that cannot be adapted is a client error
    rather than a database-level 500.
    """
    value = filter_.value
    python_type = _python_type(column)

    if python_type is None:
        return value

    if python_type in (dt.datetime, dt.date) and isinstance(value, str):
        try:
            parsed = dt.datetime.fromisoformat(value)
        except ValueError:
            raise InvalidFilterError(
                f"Value {value!r} is not a valid ISO-8601 datetime for "
                f"{filter_.canonical_name!r}.",
            ) from None
        return parsed.date() if python_type is dt.date else parsed

    # `bool` is a subclass of `int`, so check it before the numeric branch.
    if python_type is bool:
        if isinstance(value, bool):
            return value
        raise _mismatch(filter_, "boolean")

    if python_type in (int, float):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _mismatch(filter_, python_type.__name__)
        return python_type(value)

    if python_type is str and not isinstance(value, str):
        raise _mismatch(filter_, "string")

    return value


def _python_type(column: Column) -> type | None:
    try:
        return column.type.python_type
    except (NotImplementedError, AttributeError):
        return None


def _mismatch(filter_: ResolvedFilter, expected: str) -> QueryError:
    return InvalidFilterError(
        f"Value {filter_.value!r} is not valid for "
        f"{filter_.canonical_name!r}; expected a {expected}.",
    )

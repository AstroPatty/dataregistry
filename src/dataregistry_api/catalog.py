"""Schema reflection and column-name resolution.

A *namespace* is a pair of schemas, ``<namespace>_working`` and
``<namespace>_production``, with identical layouts. This module reflects them
once, then answers the two questions query construction keeps asking: which
schemas does this request address, and which table does this column name mean?

Reflection is cached per namespace. The registry schema is created by a
migration script and does not change under a running server, so paying the
introspection cost on every request would buy nothing.
"""

from __future__ import annotations

import threading

from sqlalchemy import Connection, MetaData, Table

from dataregistry_api.errors import (
    QueryError,
    UnknownNamespaceError,
    UnknownTableError,
    make_ambiguous_column_error,
    make_unknown_column_error,
)
from dataregistry_api.models import QueryMode

#: Suffixes appended to a namespace to form its two schemas.
WORKING_SUFFIX = "working"
PRODUCTION_SUFFIX = "production"

#: Which schema suffixes each query mode reads, in result order.
_MODE_SUFFIXES: dict[QueryMode, tuple[str, ...]] = {
    QueryMode.WORKING: (WORKING_SUFFIX,),
    QueryMode.PRODUCTION: (PRODUCTION_SUFFIX,),
    QueryMode.BOTH: (WORKING_SUFFIX, PRODUCTION_SUFFIX),
}

#: The table every dataset query is rooted at.
ROOT_TABLE = "dataset"

_CACHE: dict[str, "Catalog"] = {}
_CACHE_LOCK = threading.Lock()


def schema_name(namespace: str, suffix: str) -> str:
    return f"{namespace}_{suffix}"


def schemas_for_mode(namespace: str, mode: QueryMode) -> list[str]:
    """Return the schemas a query mode reads, in result order."""
    return [schema_name(namespace, suffix) for suffix in _MODE_SUFFIXES[mode]]


class Catalog:
    """Reflected metadata for one namespace.

    The two schemas of a namespace share a layout, so table and column
    *names* are taken from the working schema; only the SQLAlchemy
    :class:`~sqlalchemy.Table` objects are per-schema.
    """

    def __init__(self, namespace: str, metadata: MetaData, schemas: list[str]):
        self.namespace = namespace
        self.metadata = metadata
        self.schemas = schemas
        self._reference_schema = schemas[0]

        #: table name -> ordered column names, from the reference schema.
        self.columns_by_table: dict[str, list[str]] = {}
        for key, table in metadata.tables.items():
            if table.schema != self._reference_schema:
                continue
            self.columns_by_table[table.name] = [c.name for c in table.columns]

        #: bare column name -> the tables owning it.
        self.tables_by_column: dict[str, set[str]] = {}
        for table_name, columns in self.columns_by_table.items():
            for column in columns:
                self.tables_by_column.setdefault(column, set()).add(table_name)

    def table(self, schema: str, table_name: str) -> Table:
        return self.metadata.tables[f"{schema}.{table_name}"]

    def has_table(self, table_name: str) -> bool:
        return table_name in self.columns_by_table

    def all_column_names(self) -> set[str]:
        return set(self.tables_by_column)

    def resolve(self, names: list[str]) -> list[str]:
        """Resolve column names to canonical ``table.column`` form.

        A fully qualified name is validated and kept. A bare name is resolved
        to its owning table when only one table has it. A bare name owned by
        several tables is resolved against the *context* of the query — the
        tables the unambiguous names in the same request already pulled in —
        and is an error only if that still leaves a choice.

        This is what makes ``["name", "owner"]`` mean the two ``dataset``
        columns (``owner`` is unique to ``dataset``, so it fixes the context)
        while a lone ``["description"]``, which has no context to lean on and
        exists on three tables, is rejected.
        """
        resolved: list[str | None] = [None] * len(names)
        deferred: list[int] = []
        context: set[str] = set()

        # Pass 1: everything that can be resolved without context, which also
        # tells us which tables this query is about.
        for index, name in enumerate(names):
            parts = name.split(".")
            if len(parts) > 2:
                raise QueryError(
                    f"{name!r} is not a valid column name; "
                    "use 'column' or 'table.column'."
                )
            if len(parts) == 2:
                table_name, column_name = parts
                self._validate_qualified(name, table_name, column_name)
                resolved[index] = name
                context.add(table_name)
                continue

            candidates = self.tables_by_column.get(name)
            if not candidates:
                raise make_unknown_column_error(name, self.all_column_names())
            if len(candidates) == 1:
                table_name = next(iter(candidates))
                resolved[index] = f"{table_name}.{name}"
                context.add(table_name)
            else:
                deferred.append(index)

        # Pass 2: ambiguous bare names, narrowed by the tables in play.
        for index in deferred:
            name = names[index]
            candidates = self.tables_by_column[name]
            narrowed = candidates & context
            if len(narrowed) != 1:
                raise make_ambiguous_column_error(name, candidates)
            resolved[index] = f"{next(iter(narrowed))}.{name}"

        return [name for name in resolved if name is not None]

    def _validate_qualified(self, name: str, table_name: str, column: str) -> None:
        if not self.has_table(table_name):
            raise UnknownTableError(
                f"Unknown table {table_name!r} in column {name!r}.",
                f"Known tables: {', '.join(sorted(self.columns_by_table))}",
            )
        if column not in self.columns_by_table[table_name]:
            raise make_unknown_column_error(
                name, set(self.columns_by_table[table_name])
            )


def get_catalog(connection: Connection, namespace: str) -> Catalog:
    """Return the cached catalog for ``namespace``, reflecting it if needed."""
    cached = _CACHE.get(namespace)
    if cached is not None:
        return cached

    with _CACHE_LOCK:
        # Another thread may have populated the cache while we waited.
        cached = _CACHE.get(namespace)
        if cached is not None:
            return cached
        catalog = _reflect(connection, namespace)
        _CACHE[namespace] = catalog
        return catalog


def clear_catalog_cache() -> None:
    """Drop all reflected metadata. Intended for tests."""
    with _CACHE_LOCK:
        _CACHE.clear()


def _reflect(connection: Connection, namespace: str) -> Catalog:
    from sqlalchemy import inspect

    inspector = inspect(connection)
    available = set(inspector.get_schema_names())

    schemas = [
        schema_name(namespace, suffix)
        for suffix in (WORKING_SUFFIX, PRODUCTION_SUFFIX)
    ]
    missing = [schema for schema in schemas if schema not in available]
    if missing:
        raise UnknownNamespaceError(
            f"Unknown namespace {namespace!r}.",
            f"No such schema(s): {', '.join(missing)}",
        )

    metadata = MetaData()
    for schema in schemas:
        metadata.reflect(bind=connection, schema=schema)

    catalog = Catalog(namespace, metadata, schemas)
    if not catalog.has_table(ROOT_TABLE):
        raise UnknownNamespaceError(
            f"Namespace {namespace!r} has no {ROOT_TABLE!r} table.",
            f"Reflected schema {schemas[0]!r} is not a data registry schema.",
        )
    return catalog

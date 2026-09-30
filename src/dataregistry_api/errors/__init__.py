"""The API's error vocabulary.

Every 4xx the server emits uses the envelope the OpenAPI spec documents::

    {"error": {"code": ..., "message": ..., "detail": ...}}

Handlers signal a bad request by raising :class:`QueryError` (or one of its
subclasses); :func:`register_error_handlers` turns those into the envelope.
Raising rather than returning keeps the error path out of the happy path, and
means a column-resolution failure deep inside query construction does not have
to be threaded back out by hand.
"""

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from dataregistry.exceptions import (
    DataRegistryColumnSpec,
    DataRegistryException,
    DataRegistryNoColumn,
)
from dataregistry_api.models import Error, ErrorDetail

#: Spelled out rather than imported from `starlette.status`, whose name for
#: 422 changed between releases.
HTTP_404_NOT_FOUND = 404
HTTP_401_UNAUTHORIZED = 401
HTTP_403_FORBIDDEN = 403
HTTP_422_UNPROCESSABLE_CONTENT = 422


class QueryError(Exception):
    """A client error with a machine-readable code.

    Attributes
    ----------
    code : str
        Stable, machine-readable identifier (e.g. ``UNKNOWN_COLUMN``).
    message : str
        Human-readable summary. Echoes the offending input where possible so
        a client can tell *which* of several columns was at fault.
    detail : str or None
        Optional elaboration.
    status_code : int
        HTTP status to respond with.
    """

    status_code = HTTP_422_UNPROCESSABLE_CONTENT
    code = "INVALID_REQUEST"

    def __init__(self, message: str, detail: str | None = None):
        super().__init__(message)
        self.message = message
        self.detail = detail

    def as_error(self) -> Error:
        return Error(
            error=ErrorDetail(code=self.code, message=self.message, detail=self.detail)
        )


class UnknownNamespaceError(QueryError):
    """The requested namespace has no schemas in this database."""

    status_code = HTTP_404_NOT_FOUND
    code = "UNKNOWN_NAMESPACE"


class UnknownColumnError(QueryError):
    """A requested column does not exist."""

    code = "UNKNOWN_COLUMN"


class AmbiguousColumnError(QueryError):
    """A bare column name matches more than one table."""

    code = "AMBIGUOUS_COLUMN"


class UnknownTableError(QueryError):
    """A qualified column names a table that does not exist."""

    code = "UNKNOWN_TABLE"


class InvalidFilterError(QueryError):
    """A filter is well-formed but cannot be applied to its column."""

    code = "INVALID_FILTER"


class AuthenticationError(QueryError):
    """No acceptable credentials were supplied for a protected endpoint."""

    status_code = HTTP_401_UNAUTHORIZED
    code = "UNAUTHENTICATED"
    headers = {"WWW-Authenticate": "Bearer"}


class AuthorizationError(QueryError):
    """Credentials are valid but do not grant the requested access."""

    status_code = HTTP_403_FORBIDDEN
    code = "INSUFFICIENT_SCOPE"


def make_unknown_column_error(
    column: str, candidates: set[str] | None = None
) -> UnknownColumnError:
    """Build the error for a column that exists in no table."""
    detail = (
        f"Known columns include: {', '.join(sorted(candidates))}"
        if candidates
        else None
    )
    return UnknownColumnError(f"Unknown column {column!r}.", detail)


def make_ambiguous_column_error(
    column: str, tables: set[str] | None = None
) -> AmbiguousColumnError:
    """Build the error for a bare column name owned by several tables."""
    detail = (
        f"Qualify it as one of: {', '.join(f'{t}.{column}' for t in sorted(tables))}"
        if tables
        else None
    )
    return AmbiguousColumnError(
        f"Column {column!r} belongs to more than one table; "
        "qualify it with a table name.",
        detail,
    )


def _envelope(
    error: Error, status_code: int, headers: dict[str, str] | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code, content=jsonable_encoder(error.model_dump()), headers=headers
    )


def _query_error_response(error: QueryError) -> JSONResponse:
    """Render an API query error through the common response envelope."""
    return _envelope(error.as_error(), error.status_code, getattr(error, "headers", None))


def _namespace_from_request(request: Request) -> str | None:
    """Return a safely usable namespace from a dataset-query request."""
    namespace = request.query_params.get("namespace")
    if namespace is None:
        return None
    if not namespace or namespace.endswith(("_working", "_production")):
        return None
    if not all(char.isalnum() or char in "_-" for char in namespace):
        return None
    return namespace


def _is_missing_namespace(request: Request, exc: DataRegistryException) -> bool:
    """Whether reflection identified the request's working schema as absent."""
    namespace = _namespace_from_request(request)
    if namespace is None:
        return False
    expected = f"no Provenance table {namespace}_working.provenance"
    return str(exc).startswith("Incompatible database: ") and expected in str(exc)


def _value_error_to_query_error(exc: ValueError) -> QueryError | None:
    """Translate only stable, client-input errors raised by ``Query``."""
    message = str(exc)
    if message.startswith(("check_filter:", "Can only perform ~= search")):
        return InvalidFilterError(message)
    if message.endswith(" is not a valid column"):
        return QueryError(message)
    if message.startswith(("order_by ", "limit must be", "offset must be")):
        return QueryError(message)
    if message.startswith(("`entry_mode`", "`query_mode`", "Invalid schema name")):
        return QueryError(message)
    return None


def register_error_handlers(app: FastAPI) -> None:
    """Install handlers so every 4xx shares the documented envelope."""

    @app.exception_handler(QueryError)
    def _handle_query_error(_: Request, exc: QueryError) -> JSONResponse:
        return _query_error_response(exc)

    @app.exception_handler(DataRegistryNoColumn)
    def _handle_missing_column(_: Request, exc: DataRegistryNoColumn) -> JSONResponse:
        return _query_error_response(UnknownColumnError(str(exc)))

    @app.exception_handler(DataRegistryColumnSpec)
    def _handle_ambiguous_column(
        _: Request, exc: DataRegistryColumnSpec
    ) -> JSONResponse:
        return _query_error_response(AmbiguousColumnError(str(exc)))

    @app.exception_handler(DataRegistryException)
    def _handle_dataregistry_error(
        request: Request, exc: DataRegistryException
    ) -> JSONResponse:
        if _is_missing_namespace(request, exc):
            return _query_error_response(UnknownNamespaceError(str(exc)))
        if str(exc) == "Can only strip out table names for single table queries":
            return _query_error_response(QueryError(str(exc)))
        raise exc

    @app.exception_handler(ValueError)
    def _handle_value_error(_: Request, exc: ValueError) -> JSONResponse:
        error = _value_error_to_query_error(exc)
        if error is not None:
            return _query_error_response(error)
        raise exc

    @app.exception_handler(RequestValidationError)
    def _handle_validation_error(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # FastAPI's default 422 body is a bare `detail` list, which does not
        # match the spec. Re-wrap it, keeping pydantic's per-field diagnostics
        # as the `detail` string.
        errors = exc.errors()
        message = _summarize(errors)
        return _envelope(
            Error(
                error=ErrorDetail(
                    code="INVALID_REQUEST",
                    message=message,
                    detail=str(jsonable_encoder(errors)),
                )
            ),
            HTTP_422_UNPROCESSABLE_CONTENT,
        )


def _summarize(errors: list[dict]) -> str:
    """Condense pydantic's error list into one human-readable sentence."""
    if not errors:
        return "Request validation failed."
    first = errors[0]
    location = ".".join(str(part) for part in first.get("loc", ()) if part != "body")
    reason = first.get("msg", "is invalid")
    prefix = f"{location}: " if location else ""
    suffix = f" (and {len(errors) - 1} more)" if len(errors) > 1 else ""
    return f"{prefix}{reason}{suffix}"

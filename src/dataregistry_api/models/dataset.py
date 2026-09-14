from enum import Enum
from typing import Annotated, Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dataregistry_api.config import get_default_namespace
from dataregistry_api.models.common import Filter, QueryMode, ReturnFormat

#: Namespace used when a request does not name one. Read from the environment
#: at import time (``DATAREGISTRY_API_NAMESPACE``) so a deployment can host the
#: registry under a namespace other than ``lsst_desc``.
DEFAULT_NAMESPACE = get_default_namespace()
DEFAULT_LIMIT = 100
MAX_LIMIT = 1000


class SortDirection(str, Enum):
    ASC = "asc"
    DESC = "desc"


class SortKey(BaseModel):
    """A single sort key: a column plus a direction.

    Multiple keys are applied in the order they appear in
    ``DatasetQueryRequest.order_by``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    column: Annotated[
        str,
        Field(
            min_length=1,
            description=(
                "Column to sort by. Fully qualified (`dataset.register_date`) "
                "or a bare name resolved to its table when unambiguous."
            ),
            examples=["dataset.register_date"],
        ),
    ]
    direction: SortDirection = SortDirection.ASC

    @model_validator(mode="after")
    def _validate_column(self) -> Self:
        if not self.column.strip():
            raise ValueError("column must be non-empty")
        return self


class DatasetQueryParameters(BaseModel):
    """Query-string parameters accepted by ``POST /datasets/query``.

    Only *addressing* lives here — which namespace and which schema(s) to
    read. Everything describing the question itself (columns, filters,
    ordering, pagination, response shape) belongs to
    :class:`DatasetQueryRequest` and travels in the body.
    """

    model_config = ConfigDict(extra="forbid")

    namespace: Annotated[
        str,
        Field(
            min_length=1,
            description="Namespace (schema pair) to read from.",
        ),
    ] = DEFAULT_NAMESPACE
    query_mode: Annotated[
        QueryMode, Field(description="Which schema(s) to search.")
    ] = QueryMode.WORKING


class DatasetQueryRequest(BaseModel):
    """Body of ``POST /datasets/query``.

    Every field is optional; ``{}`` is a valid request meaning "first page of
    all ``dataset`` columns, unfiltered".
    """

    model_config = ConfigDict(extra="forbid")

    property_names: Annotated[
        list[str] | None,
        Field(description="Columns to return. Omit for all `dataset` columns."),
    ] = None
    filters: list[Filter] = Field(default_factory=list)
    order_by: Annotated[
        list[SortKey],
        Field(
            default_factory=list,
            description=(
                "Sort keys, applied in order. Unordered results are not "
                "guaranteed stable across pages, so supply a unique "
                "tie-breaker (e.g. `dataset.dataset_id`) when paginating."
            ),
        ),
    ]
    limit: Annotated[
        int,
        Field(
            ge=1,
            le=MAX_LIMIT,
            description="Maximum number of rows to return.",
        ),
    ] = DEFAULT_LIMIT
    offset: Annotated[int, Field(ge=0)] = 0
    return_format: ReturnFormat = ReturnFormat.RECORDS
    strip_table_names: Annotated[
        bool,
        Field(
            description=(
                "Return bare column names (`name`) instead of qualified ones "
                "(`dataset.name`). Only safe when the selected columns come "
                "from a single table, since qualification is what keeps "
                "`dataset.name` and `execution.name` apart."
            ),
        ),
    ] = False

    @model_validator(mode="after")
    def _validate_property_names(self) -> Self:
        if self.property_names is not None and not self.property_names:
            raise ValueError("property_names must be omitted or non-empty")
        if self.property_names is not None and any(
            not name.strip() for name in self.property_names
        ):
            raise ValueError("property_names entries must be non-empty")
        return self


#: A row of results in `records` format.
RecordsData = list[dict[str, Any]]

#: Column-oriented results in `property_dict` format.
PropertyDictData = dict[str, list[Any]]


class DatasetQueryResponse(BaseModel):
    """Body of a successful ``POST /datasets/query``."""

    model_config = ConfigDict(extra="forbid")

    format: ReturnFormat
    page_count: Annotated[
        int, Field(ge=0, description="Number of rows returned in this page.")
    ]
    total_count: Annotated[
        int | None,
        Field(
            ge=0,
            description=(
                "Total rows matching the filters, ignoring `limit`/`offset`. "
                "Requires a second counting query, so it may be null if the "
                "server declines to compute it."
            ),
        ),
    ] = None
    limit: Annotated[
        int,
        Field(
            ge=1,
            description="The `limit` actually applied (echoed for clients that omitted it).",
        ),
    ]
    offset: Annotated[int, Field(ge=0, description="The `offset` actually applied.")]
    data: Annotated[
        RecordsData | PropertyDictData,
        Field(
            description=(
                "An array of row objects for `records`, or an object of "
                "column to value-array for `property_dict`."
            ),
        ),
    ]

    @model_validator(mode="after")
    def _validate_data_shape(self) -> Self:
        if self.format is ReturnFormat.RECORDS:
            if not isinstance(self.data, list):
                raise ValueError("records format requires `data` to be a list of rows")
        elif not isinstance(self.data, dict):
            raise ValueError(
                "property_dict format requires `data` to be a mapping of "
                "column to values"
            )
        return self

    @classmethod
    def from_records(
        cls,
        records: RecordsData,
        *,
        total_count: int | None = None,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> Self:
        return cls(
            format=ReturnFormat.RECORDS,
            page_count=len(records),
            total_count=total_count,
            limit=limit,
            offset=offset,
            data=records,
        )

    @classmethod
    def from_property_dict(
        cls,
        columns: PropertyDictData,
        *,
        total_count: int | None = None,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> Self:
        page_count = len(next(iter(columns.values()))) if columns else 0
        if any(len(values) != page_count for values in columns.values()):
            raise ValueError("property_dict columns must all be the same length")
        return cls(
            format=ReturnFormat.PROPERTY_DICT,
            page_count=page_count,
            total_count=total_count,
            limit=limit,
            offset=offset,
            data=columns,
        )

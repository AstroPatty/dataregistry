from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

#: Value types a filter may be compared against. Mirrors the `oneOf` in the
#: OpenAPI `Filter.value` schema.
FilterValue = str | float | int | bool


class Operator(str, Enum):
    """Filter comparison operator.

    ``==``/``=`` and ``!=`` apply to any type; ``<``, ``<=``, ``>``, ``>=``
    require an orderable column; ``~=`` (case-insensitive) and ``~==``
    (case-sensitive) are wildcard matches.
    """

    EQ = "=="
    EQ_ALT = "="
    NE = "!="
    LT = "<"
    LE = "<="
    GT = ">"
    GE = ">="
    ILIKE = "~="
    LIKE = "~=="


#: Operators that require an orderable (numeric/datetime) column.
ORDERABLE_ONLY_OPERATORS = frozenset(
    {Operator.LT, Operator.LE, Operator.GT, Operator.GE}
)

#: Operators performing wildcard matching, restricted to specific columns.
WILDCARD_OPERATORS = frozenset({Operator.ILIKE, Operator.LIKE})

#: Wildcard character understood by the wildcard operators.
WILDCARD_CHAR = "*"


class QueryMode(str, Enum):
    """Which schema(s) of a namespace a read searches."""

    WORKING = "working"
    PRODUCTION = "production"
    BOTH = "both"


class ReturnFormat(str, Enum):
    """Shape of the rows in a query response."""

    PROPERTY_DICT = "property_dict"
    RECORDS = "records"


class Filter(BaseModel):
    """A single WHERE-clause constraint.

    Equivalent to the library's ``Filter`` namedtuple, except the operator
    field is named ``op`` rather than ``bin_op``.
    """

    model_config = ConfigDict(extra="forbid")

    property_name: Annotated[
        str,
        Field(
            min_length=1,
            description=(
                "Column to filter on. Fully qualified (`dataset.owner`) or a "
                "bare name (`owner`) resolved to its table when unambiguous."
            ),
            examples=["dataset.owner"],
        ),
    ]
    op: Annotated[Operator, Field(description="Comparison operator.")]
    value: Annotated[
        FilterValue,
        Field(description="Comparison value; type should match the column."),
    ]

    def as_library_filter(self) -> tuple[str, str, FilterValue]:
        """Return this filter in the positional form ``Query`` expects."""
        return (self.property_name, self.op.value, self.value)


class ErrorDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    detail: str | None = None


class Error(BaseModel):
    """The API's error envelope."""

    model_config = ConfigDict(extra="forbid")

    error: ErrorDetail

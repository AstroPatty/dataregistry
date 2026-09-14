"""Tests for the request/response models of ``POST /datasets/query``."""

import pytest
from pydantic import ValidationError

from dataregistry.query import _colops
from dataregistry_api.models import (
    DEFAULT_LIMIT,
    DEFAULT_NAMESPACE,
    MAX_LIMIT,
    DatasetQueryParameters,
    DatasetQueryRequest,
    DatasetQueryResponse,
    Filter,
    Operator,
    QueryMode,
    ReturnFormat,
    SortDirection,
    SortKey,
)


def test_operators_match_the_library():
    """The wire operators must be exactly those the `Query` class accepts."""
    assert {op.value for op in Operator} == set(_colops)


def test_filter_round_trips_to_the_library_form():
    filter_ = Filter(property_name="dataset.owner", op="==", value="alice")

    assert filter_.as_library_filter() == ("dataset.owner", "==", "alice")


@pytest.mark.parametrize("value", ["alice", 2, 1.5, True])
def test_filter_accepts_scalar_values(value):
    assert Filter(property_name="dataset.owner", op="==", value=value).value == value


@pytest.mark.parametrize(
    "payload",
    [
        {"property_name": "dataset.owner", "op": "==", "value": None},
        {"property_name": "dataset.owner", "op": "===", "value": "a"},
        {"property_name": "", "op": "==", "value": "a"},
        {"property_name": "dataset.owner", "op": "=="},
        {"property_name": "dataset.owner", "op": "==", "value": "a", "extra": 1},
    ],
)
def test_filter_rejects_invalid_payloads(payload):
    with pytest.raises(ValidationError):
        Filter(**payload)


def test_query_parameter_defaults():
    parameters = DatasetQueryParameters()

    assert parameters.namespace == DEFAULT_NAMESPACE
    assert parameters.query_mode is QueryMode.WORKING


def test_query_parameters_carry_only_addressing():
    """Pagination and ordering belong to the body, not the query string."""
    fields = set(DatasetQueryParameters.model_fields)

    assert fields == {"namespace", "query_mode"}


@pytest.mark.parametrize(
    "payload",
    [
        {"namespace": ""},
        {"query_mode": "everything"},
        # Moved to the body; the query string must no longer accept them.
        {"limit": 10},
        {"offset": 10},
        {"order_by": "dataset.name"},
    ],
)
def test_query_parameters_reject_invalid_values(payload):
    with pytest.raises(ValidationError):
        DatasetQueryParameters(**payload)


def test_request_defaults():
    request = DatasetQueryRequest()

    assert request.property_names is None
    assert request.filters == []
    assert request.order_by == []
    assert request.limit == DEFAULT_LIMIT
    assert request.offset == 0
    assert request.return_format is ReturnFormat.RECORDS
    assert request.strip_table_names is False


@pytest.mark.parametrize(
    "payload",
    [
        {"limit": 0},
        {"limit": MAX_LIMIT + 1},
        {"offset": -1},
        {"order_by": [{"column": "dataset.name", "direction": "sideways"}]},
        {"order_by": [{"column": ""}]},
        {"order_by": [{"column": "  "}]},
        {"order_by": [{"direction": "asc"}]},
        {"order_by": [{"column": "dataset.name", "extra": 1}]},
        # The old string form is no longer accepted.
        {"order_by": "dataset.name desc"},
    ],
)
def test_request_rejects_invalid_pagination(payload):
    with pytest.raises(ValidationError):
        DatasetQueryRequest(**payload)


def test_order_by_defaults_to_ascending():
    request = DatasetQueryRequest(order_by=[{"column": "dataset.name"}])

    assert request.order_by == [
        SortKey(column="dataset.name", direction=SortDirection.ASC)
    ]


def test_order_by_preserves_multiple_keys_in_order():
    """Multi-key sorts are the reason `order_by` is a list, not a string."""
    request = DatasetQueryRequest(
        order_by=[
            {"column": "dataset.register_date", "direction": "desc"},
            {"column": "dataset.dataset_id"},
        ]
    )

    assert [(k.column, k.direction) for k in request.order_by] == [
        ("dataset.register_date", SortDirection.DESC),
        ("dataset.dataset_id", SortDirection.ASC),
    ]


def test_request_parses_filters():
    request = DatasetQueryRequest(
        property_names=["dataset.name", "dataset.owner"],
        filters=[
            {"property_name": "dataset.owner", "op": "==", "value": "alice"},
            {"property_name": "dataset.version_major", "op": ">=", "value": 2},
        ],
    )

    assert [f.as_library_filter() for f in request.filters] == [
        ("dataset.owner", "==", "alice"),
        ("dataset.version_major", ">=", 2),
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {"property_names": []},
        {"property_names": ["  "]},
        {"return_format": "dataframe"},
        {"unknown": True},
    ],
)
def test_request_rejects_invalid_payloads(payload):
    with pytest.raises(ValidationError):
        DatasetQueryRequest(**payload)


def test_response_from_records():
    rows = [{"dataset.name": "sky", "dataset.owner": "alice"}]

    response = DatasetQueryResponse.from_records(rows)

    assert response.format is ReturnFormat.RECORDS
    assert response.page_count == 1
    assert response.data == rows


def test_response_from_property_dict():
    columns = {"dataset.name": ["sky", "cat"], "dataset.owner": ["alice", "bob"]}

    response = DatasetQueryResponse.from_property_dict(columns)

    assert response.format is ReturnFormat.PROPERTY_DICT
    assert response.page_count == 2
    assert response.data == columns


def test_response_from_empty_property_dict():
    assert DatasetQueryResponse.from_property_dict({}).page_count == 0


def test_response_echoes_pagination():
    """`limit`/`offset` are echoed so clients that omitted them see what applied."""
    response = DatasetQueryResponse.from_records([{"a": 1}], limit=50, offset=100)

    assert (response.limit, response.offset) == (50, 100)


def test_page_count_is_independent_of_total_count():
    """`page_count` counts this page; `total_count` counts all matches."""
    response = DatasetQueryResponse.from_records(
        [{"a": 1}, {"a": 2}], total_count=57, limit=2, offset=10
    )

    assert response.page_count == 2
    assert response.total_count == 57


def test_total_count_defaults_to_null():
    """The server may decline the extra counting query."""
    assert DatasetQueryResponse.from_records([{"a": 1}]).total_count is None


def test_response_rejects_ragged_property_dict():
    with pytest.raises(ValueError):
        DatasetQueryResponse.from_property_dict({"a": [1, 2], "b": [1]})


def test_response_rejects_mismatched_format_and_data():
    with pytest.raises(ValidationError):
        DatasetQueryResponse(
            format=ReturnFormat.RECORDS, page_count=0, limit=100, offset=0, data={}
        )

    with pytest.raises(ValidationError):
        DatasetQueryResponse(
            format=ReturnFormat.PROPERTY_DICT,
            page_count=0,
            limit=100,
            offset=0,
            data=[],
        )

"""Behavioural tests for ``POST /datasets/query``.

These describe the endpoint the OpenAPI spec promises, which is a slightly
tighter contract than the underlying ``Query.find_datasets``:

  - ``records`` is the default wire format, not the library's ``property_dict``.
  - ``limit`` / ``offset`` / ``order_by`` exist only at the API layer; the
    library has no pagination at all. They are *body* fields: the query string
    carries only addressing (``namespace``, ``query_mode``).
  - the library signals bad input with Python exceptions (``ValueError``,
    ``DataRegistryNoColumn``, ``DataRegistryColumnSpec``); the endpoint must
    translate those into 4xx responses with the documented error envelope
    rather than letting them escape as a 500.

The corpus under test lives in ``seed.py``; see that module for what each
dataset is there to prove.
"""

import pytest

from dataregistry.query import ILIKE_ALLOWED

from .seed import (
    PRODUCTION_DATASETS,
    STATUS_DELETED,
    STATUS_REPLACED,
    STATUS_VALID,
    WORKING_DATASETS,
)

NAME = "dataset.name"
OWNER = "dataset.owner"
VERSION = "dataset.version_string"
NFILES = "dataset.nfiles"
DATASET_ID = "dataset.dataset_id"


def names(rows) -> list[str]:
    return [row[NAME] for row in rows]


def owners(rows) -> set[str]:
    return {row[OWNER] for row in rows}


def eq(property_name, value) -> dict:
    return {"property_name": property_name, "op": "==", "value": value}


# ---------------------------------------------------------------------------
# Response envelope and formats
# ---------------------------------------------------------------------------


def test_empty_body_returns_all_working_datasets(seeded, rows):
    """An omitted body means no filters and all `dataset` columns."""
    result = rows()

    assert len(result) == len(WORKING_DATASETS)
    assert sorted(names(result)) == sorted(d.name for d in WORKING_DATASETS)


def test_empty_body_returns_every_dataset_column(seeded, rows):
    """`property_names=None` selects the whole `dataset` table, and nothing else."""
    row = rows({"filters": [eq(OWNER, "carol")]})[0]

    assert row[NAME] == "DESC:legacy:removed"
    for column in (DATASET_ID, OWNER, VERSION, "dataset.register_date"):
        assert column in row
    assert all(key.startswith("dataset.") for key in row)


def test_records_is_the_default_format(seeded, query):
    """The wire default is `records`, even though the library defaults to dicts."""
    payload = query({"filters": [eq(OWNER, "carol")]}).json()

    assert payload["format"] == "records"
    assert isinstance(payload["data"], list)
    assert isinstance(payload["data"][0], dict)


def test_page_count_matches_the_number_of_rows(seeded, query):
    payload = query({"filters": [eq(OWNER, "alice")]}).json()

    assert payload["page_count"] == len(payload["data"]) == 3


def test_total_count_ignores_pagination(seeded, query):
    """`page_count` describes the page; `total_count` describes the match set."""
    body = {"filters": [eq(OWNER, "alice")], "limit": 2}

    payload = query(body).json()

    assert payload["page_count"] == 2
    assert payload["total_count"] == 3


def test_response_echoes_the_applied_pagination(seeded, query):
    """A client that sent no `limit` still learns which one was applied."""
    payload = query().json()

    assert payload["limit"] == 100
    assert payload["offset"] == 0


def test_property_dict_format_is_column_oriented(seeded, query):
    body = {
        "property_names": [NAME, OWNER],
        "filters": [eq(OWNER, "alice")],
        "return_format": "property_dict",
    }

    payload = query(body).json()

    assert payload["format"] == "property_dict"
    assert set(payload["data"]) == {NAME, OWNER}
    assert payload["data"][OWNER] == ["alice", "alice", "alice"]
    assert payload["page_count"] == 3


def test_property_dict_of_no_matches_keeps_the_columns(seeded, query):
    """An empty result must still describe its shape, with count 0."""
    body = {
        "property_names": [NAME, OWNER],
        "filters": [eq(OWNER, "nobody")],
        "return_format": "property_dict",
    }

    payload = query(body).json()

    assert payload["page_count"] == 0
    assert set(payload["data"]) == {NAME, OWNER}
    assert payload["data"][NAME] == []


def test_no_matches_returns_an_empty_list_not_an_error(seeded, query):
    payload = query({"filters": [eq(OWNER, "nobody")]}).json()

    assert payload == {
        "format": "records",
        "page_count": 0,
        "total_count": 0,
        "limit": 100,
        "offset": 0,
        "data": [],
    }


def test_dataframe_format_is_not_offered_over_the_wire(seeded, query):
    """The library accepts `dataframe`; the API deliberately does not."""
    assert query({"return_format": "dataframe"}).status_code == 422


# ---------------------------------------------------------------------------
# Column selection
# ---------------------------------------------------------------------------


def test_property_names_selects_only_the_requested_columns(seeded, rows):
    row = rows({"property_names": [NAME, VERSION], "filters": [eq(OWNER, "carol")]})[0]

    assert set(row) == {NAME, VERSION}


def test_result_keys_are_fully_qualified_by_default(seeded, rows):
    row = rows({"property_names": ["name", "owner"], "filters": [eq(OWNER, "carol")]})[
        0
    ]

    assert set(row) == {NAME, OWNER}


def test_bare_column_names_resolve_to_their_table(seeded, rows):
    """`version_string` is unique to `dataset`, so it needs no qualification."""
    qualified = rows({"property_names": [VERSION], "filters": [eq(OWNER, "carol")]})
    bare = rows({"property_names": ["version_string"], "filters": [eq(OWNER, "carol")]})

    assert qualified == bare


def test_strip_table_names_returns_bare_keys(seeded, rows):
    body = {
        "property_names": [NAME, VERSION],
        "filters": [eq(OWNER, "carol")],
        "strip_table_names": True,
    }

    row = rows(body)[0]

    assert set(row) == {"name", "version_string"}
    assert row["name"] == "DESC:legacy:removed"


def test_strip_table_names_applies_to_property_dict_too(seeded, query):
    body = {
        "property_names": [NAME],
        "filters": [eq(OWNER, "carol")],
        "return_format": "property_dict",
        "strip_table_names": True,
    }

    payload = query(body).json()

    assert set(payload["data"]) == {"name"}


def test_strip_table_names_also_strips_the_expanded_status(seeded, rows):
    """`status`/`status_raw` are generated keys, so they must strip as well."""
    body = {
        "property_names": [NAME, "dataset.status"],
        "filters": [eq(OWNER, "carol")],
        "strip_table_names": True,
    }

    row = rows(body)[0]

    assert set(row) == {"name", "status", "status_raw"}
    assert row["status_raw"] == STATUS_VALID | STATUS_DELETED
    assert row["status"]["deleted"] is True


def test_unknown_column_is_a_client_error(seeded, query):
    response = query({"property_names": ["dataset.not_a_column"]})

    assert response.status_code == 422
    assert "not_a_column" in response.json()["error"]["message"]


def test_unknown_bare_column_is_a_client_error(seeded, query):
    response = query({"property_names": ["not_a_column"]})

    assert response.status_code == 422
    assert "not_a_column" in response.json()["error"]["message"]


def test_unknown_table_is_a_client_error(seeded, query):
    response = query({"property_names": ["not_a_table.name"]})

    assert response.status_code == 422
    assert "not_a_table" in response.json()["error"]["message"]


def test_ambiguous_bare_column_is_a_client_error(seeded, query):
    """`description` exists on `dataset`, `execution` and `keyword`."""
    response = query({"property_names": ["description"]})

    assert response.status_code == 422
    message = response.json()["error"]["message"].lower()
    assert "description" in message
    assert "table" in message


def test_malformed_column_name_is_a_client_error(seeded, query):
    response = query({"property_names": ["a.b.c"]})

    assert response.status_code == 422


def test_empty_property_names_is_a_client_error(seeded, query):
    """`[]` is distinct from omitted: it selects nothing, which is nonsense."""
    assert query({"property_names": []}).status_code == 422


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


def test_equality_filter(seeded, rows):
    assert owners(rows({"filters": [eq(OWNER, "alice")]})) == {"alice"}


@pytest.mark.parametrize("operator", ["==", "="])
def test_both_equality_spellings_behave_the_same(seeded, rows, operator):
    result = rows(
        {"filters": [{"property_name": OWNER, "op": operator, "value": "bob"}]}
    )

    assert owners(result) == {"bob"}


def test_inequality_filter(seeded, rows):
    result = rows({"filters": [{"property_name": OWNER, "op": "!=", "value": "alice"}]})

    assert owners(result) == {"bob", "carol"}


@pytest.mark.parametrize(
    ("operator", "expected"),
    [
        (">", {30, 40, 50, 60}),
        (">=", {20, 30, 40, 50, 60}),
        ("<", {10}),
        ("<=", {10, 20}),
    ],
)
def test_ordering_operators_on_a_numeric_column(seeded, rows, operator, expected):
    body = {
        "property_names": [NFILES],
        "filters": [{"property_name": NFILES, "op": operator, "value": 20}],
    }

    assert {row[NFILES] for row in rows(body)} == expected


def test_ordering_operator_on_a_string_column_is_a_client_error(seeded, query):
    """`owner` is not orderable; the library raises ValueError."""
    response = query({"filters": [{"property_name": OWNER, "op": ">", "value": "a"}]})

    assert response.status_code == 422
    assert ">" in response.json()["error"]["message"]


def test_filters_combine_with_and(seeded, rows):
    body = {
        "filters": [
            eq(OWNER, "alice"),
            {"property_name": "dataset.version_major", "op": ">=", "value": 2},
        ]
    }

    result = rows(body)

    assert sorted(names(result)) == ["DESC:sky:photometry", "DESC:sky:shear"]


def test_contradictory_filters_return_nothing(seeded, rows):
    body = {"filters": [eq(OWNER, "alice"), eq(OWNER, "bob")]}

    assert rows(body) == []


def test_a_filter_column_need_not_be_selected(seeded, rows):
    """Filtering on a column that is not in `property_names` still works."""
    body = {"property_names": [NAME], "filters": [eq(OWNER, "carol")]}

    result = rows(body)

    assert names(result) == ["DESC:legacy:removed"]
    assert set(result[0]) == {NAME}


def test_datetime_filter(seeded, rows):
    body = {
        "property_names": [NAME],
        "filters": [
            {
                "property_name": "dataset.register_date",
                "op": ">=",
                "value": "2026-01-05T00:00:00",
            }
        ],
    }

    assert sorted(names(rows(body))) == ["DESC:legacy:removed", "desc:lowercase:name"]


def test_boolean_filter(seeded, rows):
    body = {
        "property_names": [NAME],
        "filters": [eq("dataset.is_overwritable", False)],
    }

    assert len(rows(body)) == len(WORKING_DATASETS)


def test_unsupported_operator_is_rejected_before_reaching_the_database(seeded, query):
    response = query(
        {"filters": [{"property_name": OWNER, "op": "LIKE", "value": "a"}]}
    )

    assert response.status_code == 422


def test_filter_missing_a_field_is_rejected(seeded, query):
    assert query({"filters": [{"property_name": OWNER, "op": "=="}]}).status_code == 422


def test_filter_value_type_mismatch_is_a_client_error(seeded, query):
    """A string compared against an integer column must not 500."""
    body = {"filters": [eq(NFILES, "not-a-number")]}

    assert query(body).status_code in (400, 422)


# ---------------------------------------------------------------------------
# Wildcard operators
# ---------------------------------------------------------------------------


def test_case_insensitive_wildcard(seeded, rows):
    body = {
        "property_names": [NAME],
        "filters": [{"property_name": NAME, "op": "~=", "value": "desc:sky:*"}],
    }

    assert sorted(names(rows(body))) == [
        "DESC:sky:photometry",
        "DESC:sky:photometry",
        "DESC:sky:shear",
    ]


def test_case_sensitive_wildcard(seeded, rows):
    body = {
        "property_names": [NAME],
        "filters": [{"property_name": NAME, "op": "~==", "value": "desc:*"}],
    }

    assert names(rows(body)) == ["desc:lowercase:name"]


def test_wildcard_matches_in_the_middle(seeded, rows):
    body = {
        "property_names": [NAME],
        "filters": [{"property_name": NAME, "op": "~=", "value": "DESC:*:photometry"}],
    }

    assert len(rows(body)) == 2


def test_wildcard_without_a_star_is_an_exact_match(seeded, rows):
    body = {
        "property_names": [NAME],
        "filters": [{"property_name": NAME, "op": "~==", "value": "DESC:sky:shear"}],
    }

    assert names(rows(body)) == ["DESC:sky:shear"]


def test_literal_percent_is_escaped_not_treated_as_a_wildcard(seeded, rows):
    """`%` is SQL's wildcard; the API's wildcard is `*`, so `%` must be literal.

    DESC3's description contains "50%" and DESC6's contains "50 percent"; a
    broken implementation that passes `%` through matches both.
    """
    body = {
        "property_names": [NAME],
        "filters": [
            {
                "property_name": "dataset.description",
                "op": "~=",
                "value": "*50% *",
            }
        ],
    }

    assert names(rows(body)) == ["DESC:sky:shear"]


def test_literal_underscore_is_escaped(seeded, rows):
    """`_` is SQL's single-char wildcard; DESC5's "snakeZcase" is the near miss."""
    body = {
        "property_names": [NAME],
        "filters": [
            {
                "property_name": "dataset.description",
                "op": "~=",
                "value": "*snake_case*",
            }
        ],
    }

    assert names(rows(body)) == ["DESC:truth:catalog"]


@pytest.mark.parametrize("operator", ["~=", "~=="])
def test_wildcard_on_a_disallowed_column_is_a_client_error(seeded, query, operator):
    """`owner_type` is not in the library's ILIKE allowlist."""
    body = {
        "filters": [
            {"property_name": "dataset.owner_type", "op": operator, "value": "us*"}
        ]
    }

    response = query(body)

    assert response.status_code == 422
    assert "owner_type" in response.text or "~=" in response.text


@pytest.mark.parametrize("column", sorted(ILIKE_ALLOWED))
def test_wildcard_is_accepted_on_every_allowlisted_column(seeded, query, column):
    """Every column the library allows must be reachable through the API."""
    body = {"filters": [{"property_name": column, "op": "~=", "value": "*"}]}

    assert query(body).status_code == 200


def test_wildcard_against_a_non_string_value_is_a_client_error(seeded, query):
    body = {"filters": [{"property_name": NAME, "op": "~=", "value": 5}]}

    assert query(body).status_code in (400, 422)


# ---------------------------------------------------------------------------
# Joins
# ---------------------------------------------------------------------------


def test_columns_from_the_execution_table(seeded, rows):
    body = {
        "property_names": [NAME, "execution.name", "execution.site"],
        "filters": [eq("execution.site", "ALCF")],
    }

    result = rows(body)

    assert names(result) == ["DESC:truth:catalog"]
    assert result[0]["execution.name"] == "truth-pipeline"


def test_joining_execution_excludes_datasets_without_one(seeded, rows):
    """The library builds inner joins, so unattached datasets drop out."""
    result = rows({"property_names": [NAME, "execution.name"]})

    assert sorted(names(result)) == [
        "DESC:sky:photometry",
        "DESC:sky:photometry",
        "DESC:truth:catalog",
    ]


def test_columns_from_the_keyword_table(seeded, rows):
    """`keyword` is many-to-many, reached via `dataset_keyword`."""
    body = {
        "property_names": [NAME, "keyword.keyword"],
        "filters": [eq("keyword.keyword", "simulation")],
    }

    result = rows(body)

    assert sorted(names(result)) == ["DESC:sky:photometry", "DESC:sky:photometry"]
    assert {row["keyword.keyword"] for row in result} == {"simulation"}


def test_filtering_on_a_keyword_without_selecting_it(seeded, rows):
    body = {
        "property_names": [NAME],
        "filters": [eq("keyword.keyword", "observation")],
    }

    assert names(rows(body)) == ["DESC:truth:catalog"]


def test_columns_from_the_dependency_table(seeded, rows):
    """`dependency` joins on `dependency.input_id == dataset.dataset_id`."""
    body = {"property_names": [NAME, "dependency.dependency_id"]}

    result = rows(body)

    assert names(result) == ["DESC:sky:photometry"]
    assert result[0]["dependency.dependency_id"] is not None


def test_joining_three_tables_at_once(seeded, rows):
    body = {
        "property_names": [NAME, "execution.name", "keyword.keyword"],
        "filters": [eq("keyword.keyword", "observation")],
    }

    result = rows(body)

    assert names(result) == ["DESC:truth:catalog"]
    assert result[0]["execution.name"] == "truth-pipeline"


# ---------------------------------------------------------------------------
# query_mode / namespace
# ---------------------------------------------------------------------------


def test_working_is_the_default_query_mode(seeded, rows):
    assert "DESC:prod:reference" not in names(rows())


def test_production_query_mode(seeded, rows):
    result = rows(query_mode="production")

    assert names(result) == [d.name for d in PRODUCTION_DATASETS]


def test_both_query_mode_concatenates_the_schemas(seeded, rows):
    result = rows(query_mode="both")

    assert len(result) == len(WORKING_DATASETS) + len(PRODUCTION_DATASETS)
    assert "DESC:prod:reference" in names(result)


def test_both_query_mode_applies_filters_to_each_schema(seeded, rows):
    result = rows(
        {"filters": [eq("dataset.owner_type", "production")]}, query_mode="both"
    )

    assert names(result) == ["DESC:prod:reference"]


def test_both_query_mode_pages_across_the_schema_boundary(seeded, rows):
    """A page may straddle the two schemas, so the window must slide over the
    concatenation rather than be applied to each schema independently.

    `nfiles` is unique and ascending across the whole corpus (working holds
    10..60, production 70), so the boundary sits between 60 and 70 and a
    two-row page starting at offset 5 must span it.
    """
    body = {"property_names": [NFILES], "order_by": [asc(NFILES)], "limit": 2}

    straddling = rows(body | {"offset": 5}, query_mode="both")

    assert [row[NFILES] for row in straddling] == [60, 70]


def test_both_query_mode_pagination_walks_every_row_exactly_once(seeded, rows):
    """Stepping a window through `both` must reproduce the unpaginated list."""
    body = {"property_names": [NFILES], "order_by": [asc(NFILES)]}
    everything = [row[NFILES] for row in rows(body, query_mode="both")]

    walked = []
    for offset in range(0, len(everything), 2):
        page = rows(body | {"limit": 2, "offset": offset}, query_mode="both")
        walked.extend(row[NFILES] for row in page)

    assert walked == everything


def test_both_query_mode_reports_the_combined_total(seeded, query):
    """`total_count` spans both schemas even when the page only shows one."""
    payload = query({"limit": 2}, query_mode="both").json()

    assert payload["page_count"] == 2
    assert payload["total_count"] == len(WORKING_DATASETS) + len(PRODUCTION_DATASETS)


def test_invalid_query_mode_is_rejected(seeded, query):
    assert query(query_mode="everything").status_code == 422


def test_unknown_namespace_is_a_client_error(seeded, query):
    """A nonexistent namespace must not surface as a 500."""
    response = query(namespace="no_such_namespace")

    assert response.status_code in (404, 422)


# ---------------------------------------------------------------------------
# Pagination and ordering
# ---------------------------------------------------------------------------


def asc(column) -> dict:
    return {"column": column, "direction": "asc"}


def desc(column) -> dict:
    return {"column": column, "direction": "desc"}


def test_limit_caps_the_number_of_rows(seeded, query):
    payload = query({"limit": 2}).json()

    assert payload["page_count"] == len(payload["data"]) == 2


def test_offset_skips_rows(seeded, rows):
    body = {"property_names": [NFILES], "order_by": [asc(NFILES)]}
    ordered = rows(body)
    offset = rows(body | {"offset": 2})

    assert offset == ordered[2:]


def test_limit_and_offset_together_paginate(seeded, rows):
    body = {"property_names": [NFILES], "order_by": [asc(NFILES)], "limit": 2}
    page_one = rows(body)
    page_two = rows(body | {"offset": 2})

    assert [row[NFILES] for row in page_one] == [10, 20]
    assert [row[NFILES] for row in page_two] == [30, 40]


def test_offset_past_the_end_returns_nothing(seeded, rows):
    assert rows({"offset": 1000}) == []


def test_pagination_applies_to_property_dict_too(seeded, query):
    body = {
        "property_names": [NFILES],
        "return_format": "property_dict",
        "order_by": [asc(NFILES)],
        "limit": 2,
    }

    payload = query(body).json()

    assert payload["page_count"] == 2
    assert payload["data"][NFILES] == [10, 20]


def test_order_by_ascending_is_the_default(seeded, rows):
    """A key with no explicit direction sorts ascending."""
    result = rows({"property_names": [NFILES], "order_by": [{"column": NFILES}]})

    assert [row[NFILES] for row in result] == [10, 20, 30, 40, 50, 60]


def test_order_by_descending(seeded, rows):
    result = rows({"property_names": [NFILES], "order_by": [desc(NFILES)]})

    assert [row[NFILES] for row in result] == [60, 50, 40, 30, 20, 10]


def test_order_by_a_bare_column_name(seeded, rows):
    result = rows({"property_names": [NFILES], "order_by": [desc("nfiles")]})

    assert [row[NFILES] for row in result] == [60, 50, 40, 30, 20, 10]


def test_order_by_a_column_that_is_not_selected(seeded, rows):
    result = rows({"property_names": [NAME], "order_by": [desc(NFILES)]})

    assert names(result)[0] == "DESC:legacy:removed"


def test_order_by_applies_multiple_keys_in_order(seeded, rows):
    """The primary key groups; the secondary key orders within each group.

    `owner` is not unique in the corpus, so a stable secondary sort is the
    only thing that makes this assertion deterministic — which is precisely
    why `order_by` became a list.
    """
    body = {
        "property_names": [OWNER, NFILES],
        "order_by": [asc(OWNER), desc(NFILES)],
    }

    result = rows(body)
    pairs = [(row[OWNER], row[NFILES]) for row in result]

    assert pairs == sorted(pairs, key=lambda p: (p[0], -p[1]))
    assert [owner for owner, _ in pairs] == sorted(owner for owner, _ in pairs)


def test_secondary_key_breaks_ties_deterministically(seeded, rows):
    """Repeating a tied primary sort with a tie-breaker must be reproducible."""
    body = {
        "property_names": [OWNER, DATASET_ID],
        "order_by": [asc(OWNER), asc(DATASET_ID)],
    }

    assert rows(body) == rows(body)


def test_order_by_an_unknown_column_is_a_client_error(seeded, query):
    response = query({"order_by": [asc("dataset.not_a_column")]})

    assert response.status_code == 422


@pytest.mark.parametrize(
    "order_by",
    [
        # The pre-list string form must no longer be accepted.
        "dataset.name desc",
        [{"column": NAME, "direction": "sideways"}],
        [{"column": ""}],
        [{"direction": "asc"}],
        [{"column": NAME, "extra": 1}],
    ],
)
def test_malformed_order_by_is_a_client_error(seeded, query, order_by):
    assert query({"order_by": order_by}).status_code == 422


@pytest.mark.parametrize("limit", [0, -1, 1001])
def test_out_of_range_limit_is_rejected(seeded, query, limit):
    assert query({"limit": limit}).status_code == 422


def test_negative_offset_is_rejected(seeded, query):
    assert query({"offset": -1}).status_code == 422


def test_pagination_is_not_accepted_in_the_query_string(seeded, query):
    """These moved to the body; leaving them in the URL must not silently no-op."""
    for parameter in ("limit", "offset", "order_by"):
        response = query({}, **{parameter: "1"})

        assert response.status_code == 422, parameter


def test_default_limit_is_one_hundred(seeded, client):
    """The documented default; asserted against the schema, not row counts.

    The corpus is far smaller than 100, so only the spec can be checked here.
    """
    schema = client.app.openapi()["components"]["schemas"]["DatasetQueryRequest"]
    limit = schema["properties"]["limit"]

    assert limit["default"] == 100
    assert limit["maximum"] == 1000


# ---------------------------------------------------------------------------
# Status bitmask
# ---------------------------------------------------------------------------


def test_status_is_expanded_into_booleans(seeded, rows):
    """The spec expands the bitmask; `status_raw` keeps the integer."""
    body = {"property_names": [NAME, "dataset.status"], "filters": [eq(OWNER, "carol")]}

    row = rows(body)[0]

    assert row["dataset.status_raw"] == STATUS_VALID | STATUS_DELETED
    assert row["dataset.status"] == {
        "valid": True,
        "deleted": True,
        "archived": False,
        "replaced": False,
    }


def test_status_expansion_for_a_replaced_dataset(seeded, rows):
    body = {
        "property_names": [NAME, "dataset.status"],
        "filters": [eq(NAME, "DESC:sky:shear")],
    }

    row = rows(body)[0]

    assert row["dataset.status_raw"] == STATUS_VALID | STATUS_REPLACED
    assert row["dataset.status"]["replaced"] is True
    assert row["dataset.status"]["deleted"] is False


def test_status_is_expanded_when_all_columns_are_returned(seeded, rows):
    row = rows({"filters": [eq(OWNER, "carol")]})[0]

    assert isinstance(row["dataset.status"], dict)
    assert row["dataset.status_raw"] == STATUS_VALID | STATUS_DELETED


def test_status_can_still_be_filtered_as_an_integer(seeded, rows):
    """Filtering happens in SQL, so `status` there is the raw bitmask."""
    body = {
        "property_names": [NAME],
        "filters": [eq("dataset.status", STATUS_VALID | STATUS_DELETED)],
    }

    assert names(rows(body)) == ["DESC:legacy:removed"]


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


def test_datetimes_are_iso_strings(seeded, rows):
    row = rows({"property_names": [NAME, "dataset.register_date"]})[0]

    assert row["dataset.register_date"].startswith("2026-01-")


def test_nulls_are_preserved_as_null(seeded, rows):
    """DESC5 has no `access_api`; it must come back as null, not "None"."""
    body = {
        "property_names": [NAME, "dataset.access_api"],
        "filters": [eq(NAME, "desc:lowercase:name")],
    }

    assert rows(body)[0]["dataset.access_api"] is None


def test_floats_keep_their_precision(seeded, rows):
    body = {
        "property_names": ["dataset.total_disk_space"],
        "filters": [eq(NAME, "DESC:sky:shear")],
    }

    assert rows(body)[0]["dataset.total_disk_space"] == 75.25


def test_response_is_valid_against_the_declared_schema(seeded, query):
    """Whatever the endpoint returns must match its own OpenAPI response model."""
    from dataregistry_api.models import DatasetQueryResponse

    DatasetQueryResponse.model_validate(query().json())


# ---------------------------------------------------------------------------
# Malformed requests
# ---------------------------------------------------------------------------


def test_unknown_body_field_is_rejected(seeded, query):
    assert query({"nonsense": True}).status_code == 422


def test_unknown_query_parameter_is_rejected(seeded, query):
    assert query(nonsense="true").status_code == 422


def test_non_json_body_is_rejected(seeded, client):
    response = client.post(
        "/datasets/query",
        content="not json",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 422


def test_get_is_not_allowed(client):
    assert client.get("/datasets/query").status_code == 405


def test_errors_use_the_documented_envelope(seeded, query):
    """Every 4xx must be `{"error": {"code", "message"}}`, per the spec."""
    payload = query({"property_names": ["not_a_column"]}).json()

    assert set(payload) == {"error"}
    assert isinstance(payload["error"]["code"], str)
    assert isinstance(payload["error"]["message"], str)


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------


def test_query_does_not_write(seeded, client, connection):
    """A read endpoint must leave the row count untouched."""
    from sqlalchemy import text

    from .conftest import WORKING_SCHEMA

    def count() -> int:
        return connection.execute(
            text(f"SELECT count(*) FROM {WORKING_SCHEMA}.dataset")
        ).scalar_one()

    before = count()
    client.post("/datasets/query", json={"filters": [eq(OWNER, "alice")]})

    assert count() == before

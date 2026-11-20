from secagent import (
    DataScope,
    ScopeCheckStatus,
    check_data_scope,
    check_forbidden_scope,
    check_forbidden_scope_set,
)


FIELD_TYPES = {
    "department": "string",
    "expense_date": "date_iso",
    "amount": "number",
    "remark": "string",
}


def make_scope(**overrides):
    payload = {
        "schema_version": "1.0",
        "database": "company",
        "table": "expense",
        "operation": "SELECT",
        "read_columns": ["department", "expense_date", "amount"],
        "output_columns": ["expense_date", "amount"],
        "row_filter": {
            "op": "eq",
            "field": "department",
            "value": "RND",
        },
        "max_rows": 20,
    }
    payload.update(overrides)
    return DataScope.model_validate(payload)


def test_requested_field_hits_forbidden_field():
    requested = make_scope(
        read_columns=["department", "amount", "remark"],
        output_columns=["amount", "remark"],
        row_filter={"op": "true"},
    )
    forbidden = make_scope(
        read_columns=["remark"],
        output_columns=["remark"],
        row_filter={"op": "true"},
    )

    result = check_forbidden_scope(requested, forbidden, FIELD_TYPES)

    assert result.status is ScopeCheckStatus.VIOLATED
    assert result.code == "forbidden_scope_overlap"
    assert result.counterexample is not None


def test_disjoint_fields_are_proved_safe():
    requested = make_scope(
        read_columns=["department", "amount"],
        output_columns=["amount"],
        row_filter={"op": "true"},
    )
    forbidden = make_scope(
        read_columns=["remark"],
        output_columns=["remark"],
        row_filter={"op": "true"},
    )

    result = check_forbidden_scope(requested, forbidden, FIELD_TYPES)

    assert result.status is ScopeCheckStatus.PROVED
    assert result.code == "forbidden_fields_disjoint"


def test_filter_field_outside_read_columns_still_counts_as_access():
    requested = make_scope(
        read_columns=["amount"],
        output_columns=["amount"],
        row_filter={"op": "eq", "field": "department", "value": "RND"},
    )
    forbidden = make_scope(
        read_columns=["department"],
        output_columns=["department"],
        row_filter={"op": "true"},
    )

    result = check_forbidden_scope(requested, forbidden, FIELD_TYPES)

    assert result.status is ScopeCheckStatus.VIOLATED


def test_overlapping_row_filters_are_violated():
    requested = make_scope(
        read_columns=["department", "amount"],
        output_columns=["amount"],
        row_filter={
            "op": "and",
            "conditions": [
                {"op": "eq", "field": "department", "value": "RND"},
                {"op": "gte", "field": "amount", "value": 100},
            ],
        },
    )
    forbidden = make_scope(
        read_columns=["amount"],
        output_columns=["amount"],
        row_filter={
            "op": "and",
            "conditions": [
                {"op": "eq", "field": "department", "value": "RND"},
                {"op": "lte", "field": "amount", "value": 200},
            ],
        },
    )

    result = check_forbidden_scope(requested, forbidden, FIELD_TYPES)

    assert result.status is ScopeCheckStatus.VIOLATED


def test_disjoint_row_filters_are_proved_safe():
    requested = make_scope(
        read_columns=["department", "amount"],
        output_columns=["amount"],
        row_filter={"op": "eq", "field": "department", "value": "RND"},
    )
    forbidden = make_scope(
        read_columns=["amount"],
        output_columns=["amount"],
        row_filter={"op": "eq", "field": "department", "value": "SLS"},
    )

    result = check_forbidden_scope(requested, forbidden, FIELD_TYPES)

    assert result.status is ScopeCheckStatus.PROVED
    assert result.code == "forbidden_rows_disjoint"


def test_undecidable_row_overlap_is_unknown():
    requested = make_scope(
        read_columns=["department"],
        output_columns=["department"],
        row_filter={
            "op": "lt",
            "field": "department",
            "value": "SLS",
        },
    )
    forbidden = make_scope(
        read_columns=["department"],
        output_columns=["department"],
        row_filter={"op": "true"},
    )

    result = check_forbidden_scope(requested, forbidden, FIELD_TYPES)

    assert result.status is ScopeCheckStatus.UNKNOWN
    assert result.code == "forbidden_row_filter_untranslatable"


def test_one_matching_forbidden_scope_makes_set_violated():
    requested = make_scope(
        read_columns=["department", "remark"],
        output_columns=["department", "remark"],
        row_filter={"op": "true"},
    )
    forbidden_disjoint = make_scope(
        read_columns=["expense_date"],
        output_columns=["expense_date"],
        row_filter={"op": "true"},
    )
    forbidden_remark = make_scope(
        read_columns=["remark"],
        output_columns=["remark"],
        row_filter={"op": "true"},
    )

    result = check_forbidden_scope_set(
        [requested],
        [forbidden_disjoint, forbidden_remark],
        {("company", "expense"): FIELD_TYPES},
    )

    assert result.status is ScopeCheckStatus.VIOLATED
    assert result.code == "forbidden_set_violation"


def test_no_forbidden_scope_is_proved():
    result = check_forbidden_scope_set(
        [make_scope()],
        [],
        {("company", "expense"): FIELD_TYPES},
    )

    assert result.status is ScopeCheckStatus.PROVED
    assert result.code == "empty_forbidden_set"


def test_forbidden_scope_overrides_authorized_allowance():
    requested = make_scope(
        read_columns=["department", "amount"],
        output_columns=["amount"],
    )
    authorized = make_scope(
        read_columns=["department", "expense_date", "amount"],
        output_columns=["expense_date", "amount"],
    )
    forbidden = make_scope(
        read_columns=["amount"],
        output_columns=["amount"],
        row_filter={"op": "eq", "field": "department", "value": "RND"},
    )

    authorized_result = check_data_scope(
        requested,
        authorized,
        FIELD_TYPES,
    )
    forbidden_result = check_forbidden_scope(
        requested,
        forbidden,
        FIELD_TYPES,
    )

    assert authorized_result.status is ScopeCheckStatus.PROVED
    assert forbidden_result.status is ScopeCheckStatus.VIOLATED


def test_invalid_forbidden_scope_is_unknown():
    result = check_forbidden_scope(
        make_scope(),
        {"invalid": "scope"},
        FIELD_TYPES,
    )

    assert result.status is ScopeCheckStatus.UNKNOWN
    assert result.code == "invalid_datascope"


def test_max_rows_does_not_create_a_forbidden_intersection_by_itself():
    requested = make_scope(
        read_columns=["department", "amount"],
        output_columns=["amount"],
        row_filter={"op": "true"},
        max_rows=1,
    )
    forbidden = make_scope(
        read_columns=["remark"],
        output_columns=["remark"],
        row_filter={"op": "true"},
        max_rows=100,
    )

    result = check_forbidden_scope(requested, forbidden, FIELD_TYPES)

    assert result.status is ScopeCheckStatus.PROVED
    assert result.code == "forbidden_fields_disjoint"

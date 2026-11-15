from secagent import (
    DataScope,
    ScopeCheckStatus,
    Tool,
    check_data_scope,
    check_data_scope_set,
    sql_to_scope,
)


FIELD_TYPES = {
    "department": "string",
    "expense_date": "date_iso",
    "amount": "number",
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
            "op": "and",
            "conditions": [
                {"op": "eq", "field": "department", "value": "RND"},
                {
                    "op": "gte",
                    "field": "expense_date",
                    "value": "2026-11-01",
                },
            ],
        },
        "max_rows": 20,
    }
    payload.update(overrides)
    return DataScope.model_validate(payload)


def test_public_api_keeps_progent_exports_and_adds_datascope():
    assert Tool.__name__ == "Tool"
    assert DataScope.__module__ == "secagent.datascope_v1"
    assert callable(check_data_scope)
    assert callable(check_data_scope_set)
    assert callable(sql_to_scope)


def test_narrower_requested_scope_is_proved():
    requested = make_scope(
        read_columns=["department", "amount"],
        output_columns=["amount"],
    )

    result = check_data_scope(requested, make_scope(), FIELD_TYPES)

    assert result.status is ScopeCheckStatus.PROVED


def test_wider_row_filter_is_violated_with_counterexample():
    requested = make_scope(row_filter={"op": "true"})

    result = check_data_scope(requested, make_scope(), FIELD_TYPES)

    assert result.status is ScopeCheckStatus.VIOLATED
    assert result.code == "row_filter_violation"
    assert result.counterexample is not None


def test_max_rows_violation_is_explicit():
    result = check_data_scope(
        make_scope(max_rows=21),
        make_scope(max_rows=20),
        FIELD_TYPES,
    )

    assert result.status is ScopeCheckStatus.VIOLATED
    assert result.code == "max_rows_violation"
    assert result.path == "requested.max_rows"


def test_invalid_input_is_unknown():
    result = check_data_scope(
        {"invalid": "scope"},
        make_scope(),
        FIELD_TYPES,
    )

    assert result.status is ScopeCheckStatus.UNKNOWN
    assert result.code == "invalid_datascope"


def test_authorized_scopes_combine_fields_without_expanding_rows():
    requested = make_scope()
    amount_scope = make_scope(
        read_columns=["department", "expense_date", "amount"],
        output_columns=["amount"],
    )
    date_scope = make_scope(
        read_columns=["department", "expense_date", "amount"],
        output_columns=["expense_date"],
    )

    result = check_data_scope_set(
        [requested],
        [amount_scope, date_scope],
        {("company", "expense"): FIELD_TYPES},
    )

    assert result.status is ScopeCheckStatus.PROVED
    assert len(result.requested_results) == 1


def test_sql_to_scope_flows_through_the_same_authorization_checker():
    parsed = sql_to_scope(
        """
        SELECT expense_date, amount
        FROM expense
        WHERE department = ?
          AND expense_date >= ?
          AND expense_date <= ?
        LIMIT ?
        """,
        ("RND", "2026-11-01", "2026-11-30", 20),
        {"expense": FIELD_TYPES},
    )
    authorized = make_scope(
        row_filter={
            "op": "and",
            "conditions": [
                {"op": "eq", "field": "department", "value": "RND"},
                {
                    "op": "gte",
                    "field": "expense_date",
                    "value": "2026-11-01",
                },
                {
                    "op": "lte",
                    "field": "expense_date",
                    "value": "2026-11-30",
                },
            ],
        }
    )

    assert parsed.ok is True
    assert parsed.scope is not None
    result = check_data_scope(parsed.scope, authorized, FIELD_TYPES)
    assert result.status is ScopeCheckStatus.PROVED


def test_unsupported_sql_never_returns_a_scope():
    parsed = sql_to_scope(
        "SELECT e.name FROM employee e JOIN expense x "
        "ON e.employee_id = x.employee_id LIMIT 10",
        None,
        {"employee": {"employee_id": "string", "name": "string"}},
    )

    assert parsed.ok is False
    assert parsed.scope is None
    assert parsed.reason is not None
    assert "JOIN" in parsed.reason


def test_missing_limit_is_not_replaced_with_a_default():
    parsed = sql_to_scope(
        "SELECT name FROM employee WHERE employee_id = ?",
        ("E001",),
        {"employee": {"employee_id": "string", "name": "string"}},
    )

    assert parsed.ok is False
    assert parsed.scope is None
    assert parsed.reason is not None
    assert "LIMIT" in parsed.reason

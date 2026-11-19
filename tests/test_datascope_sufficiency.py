import copy
import json
from pathlib import Path

import pytest

from secagent import (
    DataScope,
    FieldSufficiencyResult,
    ScopeCheckStatus,
    check_field_sufficiency,
)


def make_scope(**overrides):
    payload = {
        "schema_version": "1.0",
        "database": "company",
        "table": "expense",
        "operation": "SELECT",
        "read_columns": ["department", "expense_date", "amount"],
        "output_columns": ["expense_date", "amount"],
        "row_filter": {"op": "eq", "field": "department", "value": "RND"},
        "max_rows": 20,
    }
    payload.update(overrides)
    return payload


def test_identical_model_and_mapping_are_field_sufficient():
    result = check_field_sufficiency(
        DataScope.model_validate(make_scope()), make_scope()
    )
    assert isinstance(result, FieldSufficiencyResult)
    assert result.status is ScopeCheckStatus.PROVED
    assert result.missing_read_columns == ()
    assert result.missing_output_columns == ()


def test_reports_missing_read_and_output_fields_together():
    requested = make_scope(
        read_columns=["expense_date"], output_columns=["expense_date"]
    )
    result = check_field_sufficiency(requested, make_scope())
    assert result.status is ScopeCheckStatus.VIOLATED
    assert result.code == "required_fields_missing"
    assert result.missing_read_columns == ("amount", "department")
    assert result.missing_output_columns == ("amount",)
    assert "amount" in result.message


def test_reading_a_field_does_not_satisfy_required_output():
    result = check_field_sufficiency(
        make_scope(output_columns=["expense_date"]), make_scope()
    )
    assert result.status is ScopeCheckStatus.VIOLATED
    assert result.missing_read_columns == ()
    assert result.missing_output_columns == ("amount",)


def test_filter_only_required_read_field_is_reported():
    result = check_field_sufficiency(
        make_scope(read_columns=["expense_date", "amount"]), make_scope()
    )
    assert result.status is ScopeCheckStatus.VIOLATED
    assert result.missing_read_columns == ("department",)
    assert result.missing_output_columns == ()


def test_extra_fields_are_sufficient_without_proving_minimality():
    result = check_field_sufficiency(
        make_scope(
            read_columns=["phone", "amount", "department", "expense_date"],
            output_columns=["phone", "expense_date", "amount"],
        ),
        make_scope(),
    )
    assert result.status is ScopeCheckStatus.PROVED
    assert "minimality" in result.message


@pytest.mark.parametrize("attribute,value", [
    ("database", "another_db"), ("table", "employee"),
])
def test_different_resource_cannot_satisfy_requirement(attribute, value):
    result = check_field_sufficiency(make_scope(**{attribute: value}), make_scope())
    assert result.status is ScopeCheckStatus.VIOLATED
    assert result.code == f"{attribute}_mismatch"


@pytest.mark.parametrize("side", ["requested", "required"])
@pytest.mark.parametrize("override", [
    {"operation": "DELETE"},
    {"schema_version": "2.0"},
    {"max_rows": 0},
    {"max_rows": True},
    {"read_columns": ["expense_date"]},
    {"output_columns": ["amount", "amount"]},
    {"row_filter": {"op": "or", "conditions": []}},
])
def test_invalid_scope_on_either_side_is_unknown(side, override):
    bad = make_scope(**override)
    requested, required = (bad, make_scope()) if side == "requested" else (make_scope(), bad)
    result = check_field_sufficiency(requested, required)
    assert result.status is ScopeCheckStatus.UNKNOWN
    assert result.path == f"{side}_scope"
    assert result.missing_read_columns == result.missing_output_columns == ()


@pytest.mark.parametrize("bad", [None, [], "SELECT amount FROM expense", {}])
def test_bad_input_is_unknown(bad):
    assert check_field_sufficiency(bad, make_scope()).status is ScopeCheckStatus.UNKNOWN


def test_mutated_model_is_revalidated():
    requested = DataScope.model_validate(make_scope())
    requested.read_columns.remove("amount")
    result = check_field_sufficiency(requested, make_scope())
    assert result.status is ScopeCheckStatus.UNKNOWN


def test_valid_row_conditions_and_limits_are_not_compared():
    requested = make_scope(
        row_filter={"op": "eq", "field": "department", "value": "SLS"},
        max_rows=1,
    )
    result = check_field_sufficiency(requested, make_scope(max_rows=200))
    assert result.status is ScopeCheckStatus.PROVED
    assert "row completeness" in result.message


def test_inputs_are_not_modified_and_field_order_does_not_matter():
    requested = make_scope()
    required = DataScope.model_validate(make_scope())
    before = copy.deepcopy(requested)
    before_required = required.model_dump()
    assert check_field_sufficiency(requested, required).status is ScopeCheckStatus.PROVED
    assert requested == before
    assert required.model_dump() == before_required


@pytest.mark.parametrize("task_id", ["DB-001", "DB-006"])
def test_real_taskspec_required_fields_and_missing_amount(task_id):
    spec_path = (
        Path(__file__).resolve().parents[1]
        / "data_minimization_benchmark" / "dataset" / "task_specs"
        / f"{task_id}.json"
    )
    required = json.loads(spec_path.read_text(encoding="utf-8"))["must_visit"]["datascope"][0]
    assert check_field_sufficiency(required, required).status is ScopeCheckStatus.PROVED
    requested = copy.deepcopy(required)
    requested["read_columns"].remove("amount")
    requested["output_columns"].remove("amount")
    result = check_field_sufficiency(requested, required)
    assert result.status is ScopeCheckStatus.VIOLATED
    assert result.missing_read_columns == result.missing_output_columns == ("amount",)

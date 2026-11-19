import copy
import json
from pathlib import Path

import pytest

from secagent import (
    DataScope, ExtraFieldsResult, FieldUsage, ScopeCheckStatus,
    FieldMinimalityResult, FieldRemovalAttempt, check_field_minimality,
    check_field_sufficiency, find_extra_fields, sql_to_scope,
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


def usage(result, field):
    return next(item for item in result.field_usages if item.field == field)


def test_required_filter_only_read_is_not_redundant():
    result = find_extra_fields(DataScope.model_validate(make_scope()), make_scope())
    assert isinstance(result, ExtraFieldsResult)
    assert result.status is ScopeCheckStatus.PROVED
    assert result.extra_read_columns == result.extra_output_columns == ()
    department = usage(result, "department")
    assert isinstance(department, FieldUsage)
    assert department.required_read and department.required_filter
    assert department.requested_filter and not department.requested_output
    assert "task_required_filter" in department.reasons


def test_reading_and_outputting_extra_field_are_separate_findings():
    requested = make_scope(
        read_columns=["phone", "department", "expense_date", "amount"],
        output_columns=["phone", "expense_date", "amount"],
    )
    result = find_extra_fields(requested, make_scope())
    assert result.status is ScopeCheckStatus.VIOLATED
    assert result.extra_read_columns == result.extra_output_columns == ("phone",)
    assert "requested_output_dependency" in usage(result, "phone").reasons
    assert not usage(result, "phone").required_read
    assert check_field_sufficiency(requested, make_scope()).status is ScopeCheckStatus.PROVED


def test_read_only_extra_field():
    result = find_extra_fields(
        make_scope(read_columns=["department", "expense_date", "amount", "phone"]),
        make_scope(),
    )
    assert result.extra_read_columns == ("phone",)
    assert result.extra_output_columns == ()
    assert not usage(result, "phone").requested_output


def test_outputting_required_filter_field_is_only_extra_output():
    result = find_extra_fields(
        make_scope(output_columns=["department", "expense_date", "amount"]),
        make_scope(),
    )
    assert result.extra_read_columns == ()
    assert result.extra_output_columns == ("department",)
    assert usage(result, "department").required_filter


@pytest.mark.parametrize("declare_read", [False, True])
def test_extra_filter_is_reported_even_outside_explicit_read_list(declare_read):
    requested = make_scope(
        row_filter={"op": "and", "conditions": [
            {"op": "eq", "field": "department", "value": "RND"},
            {"op": "eq", "field": "phone", "value": "123"},
        ]},
    )
    if declare_read:
        requested["read_columns"].append("phone")
    result = find_extra_fields(requested, make_scope())
    assert result.status is ScopeCheckStatus.VIOLATED
    assert result.extra_read_columns == (("phone",) if declare_read else ())
    assert result.extra_filter_columns == ("phone",)
    phone = usage(result, "phone")
    assert phone.requested_filter and not phone.required_read
    assert "requested_filter_dependency" in phone.reasons
    assert "filter_access_not_required_by_task" in phone.reasons


def test_undeclared_required_filter_dependency_is_unknown_without_repair():
    required = make_scope(read_columns=["expense_date", "amount"])
    before = copy.deepcopy(required)
    result = find_extra_fields(make_scope(), required)
    assert result.status is ScopeCheckStatus.UNKNOWN
    assert result.code == "required_filter_fields_not_declared"
    assert result.undeclared_required_filter_columns == ("department",)
    assert result.path == "required_scope.read_columns"
    assert result.extra_read_columns == ()
    assert required == before


@pytest.mark.parametrize("op", ["eq", "in", "lt", "lte", "gt", "gte"])
def test_filter_leaf_operators_preserve_dependencies(op):
    condition = {"op": op, "field": "amount"}
    condition.update({"values": [1, 2]} if op == "in" else {"value": 1})
    result = find_extra_fields(make_scope(row_filter=condition), make_scope())
    assert result.status is ScopeCheckStatus.PROVED
    assert usage(result, "amount").requested_filter


def test_nested_filters_deduplicate_fields_and_sort_evidence():
    requested = make_scope(row_filter={"op": "and", "conditions": [
        {"op": "eq", "field": "department", "value": "RND"},
        {"op": "and", "conditions": [
            {"op": "gte", "field": "expense_date", "value": "2026-11-01"},
            {"op": "lte", "field": "expense_date", "value": "2026-11-30"},
        ]},
    ]})
    result = find_extra_fields(requested, make_scope())
    assert tuple(item.field for item in result.field_usages) == (
        "amount", "department", "expense_date",
    )
    assert usage(result, "expense_date").requested_filter


def test_missing_fields_do_not_hide_extras_or_imply_sufficiency():
    requested = make_scope(read_columns=["phone"], output_columns=["phone"],
                           row_filter={"op": "true"})
    result = find_extra_fields(requested, make_scope())
    assert result.extra_read_columns == result.extra_output_columns == ("phone",)
    assert check_field_sufficiency(requested, make_scope()).status is ScopeCheckStatus.VIOLATED


def test_no_extras_does_not_mean_field_sufficiency():
    requested = make_scope(read_columns=["amount"], output_columns=["amount"],
                           row_filter={"op": "true"})
    result = find_extra_fields(requested, make_scope())
    assert result.status is ScopeCheckStatus.PROVED
    assert not usage(result, "expense_date").requested_read
    assert check_field_sufficiency(requested, make_scope()).status is ScopeCheckStatus.VIOLATED
    assert "not proved" in result.message


@pytest.mark.parametrize("attribute,value", [
    ("database", "other"), ("table", "employee"),
])
def test_unrelated_resource_is_incomparable(attribute, value):
    result = find_extra_fields(make_scope(**{attribute: value}), make_scope())
    assert result.status is ScopeCheckStatus.UNKNOWN
    assert result.code == f"{attribute}_mismatch"
    assert result.field_usages == ()


@pytest.mark.parametrize("side", ["requested", "required"])
@pytest.mark.parametrize("bad", [
    None, {}, [], make_scope(operation="DELETE"),
    make_scope(schema_version="2.0"), make_scope(max_rows=True),
    make_scope(read_columns=["amount"]),
    make_scope(row_filter={"op": "or", "conditions": []}),
])
def test_invalid_inputs_return_unknown(side, bad):
    requested, required = (bad, make_scope()) if side == "requested" else (make_scope(), bad)
    result = find_extra_fields(requested, required)
    assert result.status is ScopeCheckStatus.UNKNOWN
    assert result.path == f"{side}_scope"


def test_mutated_model_is_revalidated():
    requested = DataScope.model_validate(make_scope())
    requested.output_columns.append("phone")
    assert find_extra_fields(requested, make_scope()).status is ScopeCheckStatus.UNKNOWN


def test_inputs_are_unchanged_and_row_values_and_limits_are_not_compared():
    requested = make_scope(max_rows=1,
        row_filter={"op": "eq", "field": "department", "value": "SLS"})
    required = DataScope.model_validate(make_scope())
    before = copy.deepcopy(requested)
    before_required = required.model_dump()
    assert find_extra_fields(requested, required).status is ScopeCheckStatus.PROVED
    assert requested == before
    assert required.model_dump() == before_required


@pytest.mark.parametrize("task_id", ["DB-001", "DB-006"])
def test_real_taskspec_and_sql_extras(task_id):
    path = (Path(__file__).resolve().parents[1] / "data_minimization_benchmark"
            / "dataset" / "task_specs" / f"{task_id}.json")
    required = json.loads(path.read_text(encoding="utf-8"))["must_visit"]["datascope"][0]
    schema = {"expense": {field: "string" for field in
              ["department", "expense_date", "amount", "phone"]}}
    converted = sql_to_scope(
        "SELECT expense_date, amount, phone FROM expense "
        "WHERE department = 'RND' LIMIT 20", None, schema,
    )
    assert converted.ok
    result = find_extra_fields(converted.scope, required)
    assert result.extra_read_columns == result.extra_output_columns == ("phone",)
    assert usage(result, "department").required_filter


def test_minimality_keeps_required_fields_and_records_failed_deletions():
    result = check_field_minimality(make_scope(), make_scope())
    assert isinstance(result, FieldMinimalityResult)
    assert result.status is ScopeCheckStatus.PROVED
    assert result.code == "field_minimality_proved"
    assert result.candidate_one_step_minimal is True
    assert result.removed_read_columns == result.removed_output_columns == ()
    amount = next(a for a in result.attempts
                  if a.phase == "reduction" and a.operation == "remove_output"
                  and a.field == "amount")
    assert isinstance(amount, FieldRemovalAttempt)
    assert not amount.accepted
    assert amount.sufficiency.missing_output_columns == ("amount",)
    department = next(a for a in result.attempts
                      if a.phase == "reduction" and a.operation == "remove_read"
                      and a.field == "department")
    assert department.code == "field_dependency"
    assert "filter" in department.message


def test_remove_extra_output_then_unlocked_read_and_revalidate():
    requested = make_scope(
        read_columns=["department", "expense_date", "amount", "phone"],
        output_columns=["expense_date", "amount", "phone"],
    )
    result = check_field_minimality(requested, make_scope())
    assert result.status is ScopeCheckStatus.VIOLATED
    assert result.code == "removable_fields_found"
    assert result.removed_read_columns == result.removed_output_columns == ("phone",)
    assert result.reduced_scope == DataScope.model_validate(make_scope())
    assert result.final_sufficiency.status is ScopeCheckStatus.PROVED
    assert result.final_extra_fields.status is ScopeCheckStatus.PROVED
    assert result.candidate_one_step_minimal is True
    accepted = [a for a in result.attempts if a.accepted]
    assert [(a.operation, a.field) for a in accepted] == [
        ("remove_output", "phone"), ("remove_read", "phone"),
    ]
    assert "phone" in accepted[0].candidate_scope.read_columns
    assert "phone" not in accepted[1].candidate_scope.read_columns
    for attempt in accepted:
        DataScope.model_validate(attempt.candidate_scope.model_dump())
        assert attempt.sufficiency.status is ScopeCheckStatus.PROVED


def test_remove_extra_output_but_keep_required_filter_read():
    result = check_field_minimality(
        make_scope(output_columns=["department", "expense_date", "amount"]),
        make_scope(),
    )
    assert result.removed_output_columns == ("department",)
    assert result.removed_read_columns == ()
    assert "department" in result.reduced_scope.read_columns
    assert result.final_extra_fields.status is ScopeCheckStatus.PROVED


def test_remove_read_only_extra_field():
    result = check_field_minimality(
        make_scope(read_columns=["department", "expense_date", "amount", "phone"]),
        make_scope(),
    )
    assert result.removed_read_columns == ("phone",)
    assert result.removed_output_columns == ()


@pytest.mark.parametrize("declare_read", [False, True])
def test_extra_filter_dependency_remains_and_prevents_task_minimality(declare_read):
    requested = make_scope(row_filter={"op": "eq", "field": "phone", "value": "123"})
    if declare_read:
        requested["read_columns"].append("phone")
        requested["output_columns"].append("phone")
    result = check_field_minimality(requested, make_scope())
    assert result.status is ScopeCheckStatus.VIOLATED
    assert result.code == "extra_filter_dependencies_remain"
    assert result.candidate_one_step_minimal is True
    assert result.final_extra_fields.extra_filter_columns == ("phone",)
    assert result.reduced_scope.row_filter.model_dump() == requested["row_filter"]
    assert result.removed_read_columns == ()
    if declare_read:
        assert result.removed_output_columns == ("phone",)
        assert "phone" in result.reduced_scope.read_columns
        assert any(a.field == "phone" and a.code == "field_dependency"
                   for a in result.attempts)
    else:
        assert "phone" not in result.reduced_scope.read_columns


def test_initial_insufficiency_stops_before_reduction_and_reports_missing():
    requested = make_scope(read_columns=["expense_date", "phone"],
                           output_columns=["expense_date", "phone"],
                           row_filter={"op": "true"})
    result = check_field_minimality(requested, make_scope())
    assert result.status is ScopeCheckStatus.VIOLATED
    assert result.code == "required_fields_missing"
    assert result.initial_sufficiency.missing_read_columns == ("amount", "department")
    assert result.initial_sufficiency.missing_output_columns == ("amount",)
    assert result.reduced_scope is None
    assert result.candidate_one_step_minimal is None
    assert result.attempts == ()


def test_output_insufficiency_stops_even_if_reads_are_complete():
    result = check_field_minimality(
        make_scope(output_columns=["expense_date"]), make_scope(),
    )
    assert result.initial_sufficiency.missing_read_columns == ()
    assert result.initial_sufficiency.missing_output_columns == ("amount",)
    assert result.reduced_scope is None


@pytest.mark.parametrize("side", ["requested", "required"])
@pytest.mark.parametrize("bad", [
    None, {}, [], make_scope(operation="DELETE"),
    make_scope(schema_version="2.0"), make_scope(max_rows=True),
    make_scope(read_columns=["amount"]),
    make_scope(row_filter={"op": "or", "conditions": []}),
])
def test_minimality_invalid_inputs_are_unknown_without_trials(side, bad):
    requested, required = (bad, make_scope()) if side == "requested" else (make_scope(), bad)
    result = check_field_minimality(requested, required)
    assert result.status is ScopeCheckStatus.UNKNOWN
    assert result.path == f"{side}_scope"
    assert result.attempts == ()
    assert result.reduced_scope is None


@pytest.mark.parametrize("attribute,value", [
    ("database", "other"), ("table", "employee"),
])
def test_minimality_resource_mismatch_stops(attribute, value):
    result = check_field_minimality(make_scope(**{attribute: value}), make_scope())
    assert result.status is ScopeCheckStatus.VIOLATED
    assert result.code == f"{attribute}_mismatch"
    assert result.reduced_scope is None


def test_inconsistent_required_filter_returns_unknown_without_repair():
    required = make_scope(read_columns=["expense_date", "amount"])
    before = copy.deepcopy(required)
    result = check_field_minimality(make_scope(), required)
    assert result.initial_sufficiency.status is ScopeCheckStatus.PROVED
    assert result.status is ScopeCheckStatus.UNKNOWN
    assert result.code == "required_filter_fields_not_declared"
    assert result.final_extra_fields.undeclared_required_filter_columns == ("department",)
    assert result.reduced_scope is None
    assert result.attempts == ()
    assert required == before


def test_single_output_cannot_create_empty_scope():
    scope = make_scope(read_columns=["amount"], output_columns=["amount"],
                       row_filter={"op": "true"})
    result = check_field_minimality(scope, scope)
    assert result.status is ScopeCheckStatus.PROVED
    assert result.reduced_scope.output_columns == ["amount"]
    assert any(a.operation == "remove_output" and a.code == "invalid_candidate"
               and a.candidate_scope is None for a in result.attempts)


def test_required_read_without_output_or_filter_is_still_preserved():
    required = make_scope(
        read_columns=["amount", "category"], output_columns=["amount"],
        row_filter={"op": "true"},
    )
    result = check_field_minimality(required, required)
    trial = next(a for a in result.attempts
                 if a.operation == "remove_read" and a.field == "category")
    assert not trial.accepted
    assert trial.sufficiency.missing_read_columns == ("category",)
    assert result.status is ScopeCheckStatus.PROVED


def test_final_recheck_covers_every_remaining_operation_on_final_snapshot():
    requested = make_scope(
        read_columns=["department", "expense_date", "amount", "phone", "remark"],
        output_columns=["expense_date", "amount", "phone"],
    )
    result = check_field_minimality(requested, make_scope())
    final_checks = [a for a in result.attempts if a.phase == "final_recheck"]
    expected = {("remove_output", f) for f in result.reduced_scope.output_columns}
    expected |= {("remove_read", f) for f in result.reduced_scope.read_columns}
    assert {(a.operation, a.field) for a in final_checks} == expected
    assert all(not a.accepted for a in final_checks)
    assert all(a.scope_before == result.reduced_scope for a in final_checks)


def test_original_inputs_and_evidence_snapshots_do_not_alias_reduced_scope():
    requested = DataScope.model_validate(make_scope(
        read_columns=["department", "expense_date", "amount", "phone"],
        output_columns=["expense_date", "amount", "phone"],
    ))
    required = make_scope()
    before_requested = requested.model_dump()
    before_required = copy.deepcopy(required)
    result = check_field_minimality(requested, required)
    assert requested.model_dump() == before_requested
    assert required == before_required
    assert result.reduced_scope is not requested
    snapshots = [copy.deepcopy(a.scope_before.model_dump()) for a in result.attempts]
    candidates = [a.candidate_scope.model_dump() if a.candidate_scope else None
                  for a in result.attempts]
    result.reduced_scope.read_columns.append("other")
    assert [a.scope_before.model_dump() for a in result.attempts] == snapshots
    assert [a.candidate_scope.model_dump() if a.candidate_scope else None
            for a in result.attempts] == candidates
    assert requested.model_dump() == before_requested


def test_row_predicates_and_limits_are_preserved_not_verified():
    requested = make_scope(max_rows=1,
        row_filter={"op": "eq", "field": "department", "value": "SLS"})
    result = check_field_minimality(requested, make_scope())
    assert result.status is ScopeCheckStatus.PROVED
    assert result.reduced_scope.max_rows == 1
    assert result.reduced_scope.row_filter.value == "SLS"
    assert "Row completeness" in result.message


def test_reduction_is_idempotent_for_all_small_extra_field_combinations():
    # Per extra field: absent, read-only, or read+output. Independently check
    # the nine cases against the supplied requirement, not against C2 output.
    import itertools

    required = make_scope()
    for states in itertools.product(range(3), repeat=2):
        requested = copy.deepcopy(required)
        for field, state in zip(("phone", "remark"), states):
            if state >= 1:
                requested["read_columns"].append(field)
            if state == 2:
                requested["output_columns"].append(field)
        result = check_field_minimality(requested, required)
        assert result.reduced_scope == DataScope.model_validate(required)
        assert result.candidate_one_step_minimal is True
        repeated = check_field_minimality(result.reduced_scope, required)
        assert repeated.status is ScopeCheckStatus.PROVED
        assert repeated.removed_read_columns == repeated.removed_output_columns == ()


@pytest.mark.parametrize("task_id", ["DB-001", "DB-006"])
def test_task_spec_fields_with_extra_phone_are_reduced_without_mutating_truth(task_id):
    path = (Path(__file__).resolve().parents[1] / "data_minimization_benchmark"
            / "dataset" / "task_specs" / f"{task_id}.json")
    required = json.loads(path.read_text(encoding="utf-8"))["must_visit"]["datascope"][0]
    before = copy.deepcopy(required)
    requested = copy.deepcopy(required)
    requested["read_columns"].append("phone")
    requested["output_columns"].append("phone")
    result = check_field_minimality(requested, required)
    assert result.status is ScopeCheckStatus.VIOLATED
    assert result.reduced_scope == DataScope.model_validate(required)
    assert result.candidate_one_step_minimal is True
    assert result.final_sufficiency.status is ScopeCheckStatus.PROVED
    assert required == before

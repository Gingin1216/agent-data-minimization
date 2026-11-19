import copy
import importlib
import json
from pathlib import Path

import pytest
import z3

from secagent import (
    DataScope, RowScopeComparisonResult, ScopeCheckStatus, check_row_scope,
)


TYPES = {"department": "string", "expense_date": "date_iso", "amount": "number"}


def make_scope(row_filter=None, **overrides):
    scope = {
        "schema_version": "1.0", "database": "company", "table": "expense",
        "operation": "SELECT",
        "read_columns": ["department", "expense_date", "amount"],
        "output_columns": ["expense_date", "amount"],
        "row_filter": row_filter if row_filter is not None else {"op": "true"},
        "max_rows": 20,
    }
    scope.update(overrides)
    return scope


def department(value):
    return {"op": "eq", "field": "department", "value": value}


def period(start, end):
    return {"op": "and", "conditions": [
        department("RND"),
        {"op": "gte", "field": "expense_date", "value": start},
        {"op": "lte", "field": "expense_date", "value": end},
    ]}


def assert_directions(result, coverage, no_extra):
    assert result.coverage.status is coverage
    assert result.no_extra.status is no_extra
    for direction in (result.coverage, result.no_extra):
        if direction.status is ScopeCheckStatus.VIOLATED:
            assert direction.counterexample is not None
        if direction.status is ScopeCheckStatus.PROVED:
            assert direction.counterexample is None


def test_equal_predicates_with_different_syntax():
    result = check_row_scope(
        DataScope.model_validate(make_scope(department("RND"))),
        make_scope({"op": "in", "field": "department", "values": ["RND"]}), TYPES,
    )
    assert isinstance(result, RowScopeComparisonResult)
    assert result.status is ScopeCheckStatus.PROVED
    assert result.code == "row_predicates_equivalent"
    assert_directions(result, ScopeCheckStatus.PROVED, ScopeCheckStatus.PROVED)


def test_year_request_covers_month_but_has_extras():
    result = check_row_scope(
        make_scope(period("2026-01-01", "2026-12-31")),
        make_scope(period("2026-11-01", "2026-11-30")), TYPES,
    )
    assert_directions(result, ScopeCheckStatus.PROVED, ScopeCheckStatus.VIOLATED)
    assert result.no_extra.code == "extra_rows_requested"
    assert result.status is ScopeCheckStatus.VIOLATED


def test_day_request_omits_month_without_extras():
    result = check_row_scope(
        make_scope(period("2026-11-01", "2026-11-01")),
        make_scope(period("2026-11-01", "2026-11-30")), TYPES,
    )
    assert_directions(result, ScopeCheckStatus.VIOLATED, ScopeCheckStatus.PROVED)
    assert result.coverage.code == "required_rows_missing"


def test_wrong_department_has_both_omissions_and_extras():
    result = check_row_scope(make_scope(department("SLS")), make_scope(department("RND")), TYPES)
    assert_directions(result, ScopeCheckStatus.VIOLATED, ScopeCheckStatus.VIOLATED)
    assert "RND" in result.coverage.counterexample
    assert "SLS" in result.no_extra.counterexample


@pytest.mark.parametrize("requested_all", [True, False])
def test_unbounded_predicate_containment(requested_all):
    all_rows, rnd = make_scope(), make_scope(department("RND"))
    requested, required = (all_rows, rnd) if requested_all else (rnd, all_rows)
    result = check_row_scope(requested, required, TYPES)
    assert_directions(
        result,
        ScopeCheckStatus.PROVED if requested_all else ScopeCheckStatus.VIOLATED,
        ScopeCheckStatus.VIOLATED if requested_all else ScopeCheckStatus.PROVED,
    )


@pytest.mark.parametrize("empty_side", ["requested", "required", "both"])
def test_contradictory_predicates_use_empty_set_semantics(empty_side):
    empty = make_scope({"op": "and", "conditions": [department("RND"), department("SLS")]})
    requested = empty if empty_side in ("requested", "both") else make_scope()
    required = empty if empty_side in ("required", "both") else make_scope()
    result = check_row_scope(requested, required, TYPES)
    assert_directions(
        result,
        ScopeCheckStatus.VIOLATED if empty_side == "requested" else ScopeCheckStatus.PROVED,
        ScopeCheckStatus.VIOLATED if empty_side == "required" else ScopeCheckStatus.PROVED,
    )


def test_boundary_closed_vs_open_preserves_exact_missing_witness():
    required = make_scope({"op": "gte", "field": "amount", "value": 100})
    requested = make_scope({"op": "gt", "field": "amount", "value": 100})
    result = check_row_scope(requested, required, TYPES)
    assert_directions(result, ScopeCheckStatus.VIOLATED, ScopeCheckStatus.PROVED)
    assert "field_amount = 100" in result.coverage.counterexample


@pytest.mark.parametrize("field_types", [
    None, [], {}, {"department": "TEXT"}, {"department": 1},
])
def test_missing_or_invalid_field_types_are_unknown(field_types):
    result = check_row_scope(make_scope(department("RND")), make_scope(), field_types)
    assert_directions(result, ScopeCheckStatus.UNKNOWN, ScopeCheckStatus.UNKNOWN)
    assert result.coverage.unsupported_reason
    assert result.coverage.message


@pytest.mark.parametrize("side", ["requested", "required"])
@pytest.mark.parametrize("bad", [
    None, {}, [], make_scope(operation="DELETE"), make_scope(schema_version="2.0"),
    make_scope(max_rows=True), make_scope(read_columns=["amount"]),
    make_scope({"op": "or", "conditions": []}),
])
def test_invalid_scope_is_unknown_on_both_directions(side, bad):
    requested, required = (bad, make_scope()) if side == "requested" else (make_scope(), bad)
    result = check_row_scope(requested, required, TYPES)
    assert result.status is ScopeCheckStatus.UNKNOWN
    assert result.coverage.path == f"{side}_scope"
    assert_directions(result, ScopeCheckStatus.UNKNOWN, ScopeCheckStatus.UNKNOWN)


@pytest.mark.parametrize("side", ["requested", "required"])
def test_string_order_comparison_is_unknown_with_reason(side):
    ordered = make_scope({"op": "gte", "field": "department", "value": "RND"})
    requested, required = (ordered, make_scope()) if side == "requested" else (make_scope(), ordered)
    result = check_row_scope(requested, required, TYPES)
    assert result.status is ScopeCheckStatus.UNKNOWN
    assert result.coverage.path == f"{side}_scope.row_filter"
    assert "collation" in result.coverage.message


@pytest.mark.parametrize("attribute,value", [("table", "employee"), ("database", "other")])
def test_incomparable_resources_are_unknown(attribute, value):
    result = check_row_scope(make_scope(**{attribute: value}), make_scope(), TYPES)
    assert result.status is ScopeCheckStatus.UNKNOWN
    assert result.coverage.unsupported_reason == "incomparable_resources"


@pytest.mark.parametrize("timeout", [True, False, 0, -1, 1.5, None])
def test_invalid_timeout_is_unknown(timeout):
    result = check_row_scope(make_scope(), make_scope(), TYPES, timeout_ms=timeout)
    assert result.status is ScopeCheckStatus.UNKNOWN
    assert result.coverage.code == "invalid_timeout"


def test_mutated_scope_is_revalidated():
    requested = DataScope.model_validate(make_scope())
    requested.output_columns.append("phone")
    result = check_row_scope(requested, make_scope(), TYPES)
    assert result.status is ScopeCheckStatus.UNKNOWN


def test_fields_and_limits_are_not_compared_and_inputs_remain_unchanged():
    requested = DataScope.model_validate(make_scope(
        department("RND"), read_columns=["amount"], output_columns=["amount"], max_rows=1,
    ))
    required = make_scope(department("RND"), max_rows=200)
    before = requested.model_dump()
    before_required = copy.deepcopy(required)
    result = check_row_scope(requested, required, TYPES)
    assert result.status is ScopeCheckStatus.PROVED
    assert requested.model_dump() == before
    assert required == before_required
    assert "LIMIT" in result.message


def test_both_translations_receive_same_variable_map(monkeypatch):
    module = importlib.import_module("secagent.datascope_row_check")
    original = module.row_filter_to_z3
    seen = []

    def traced(node, types, variables):
        seen.append(variables)
        return original(node, types, variables)

    monkeypatch.setattr(module, "row_filter_to_z3", traced)
    assert check_row_scope(make_scope(), make_scope(), TYPES).status is ScopeCheckStatus.PROVED
    assert len(seen) == 2 and seen[0] is seen[1]


@pytest.mark.parametrize("other_violated", [True, False])
def test_one_unknown_direction_does_not_erase_other_result(monkeypatch, other_violated):
    module = importlib.import_module("secagent.datascope_row_check")
    real_solver = z3.Solver
    created = []

    class UnknownSolver:
        def set(self, *args):
            assert args == ("timeout", 37)

        def add(self, *args):
            pass

        def check(self):
            return z3.unknown

        def reason_unknown(self):
            return "timeout (simulated)"

    def factory():
        created.append(True)
        return UnknownSolver() if len(created) == 1 else real_solver()

    monkeypatch.setattr(module.z3, "Solver", factory)
    requested = make_scope() if other_violated else make_scope(department("RND"))
    result = check_row_scope(requested, make_scope(department("RND")), TYPES, timeout_ms=37)
    assert result.coverage.status is ScopeCheckStatus.UNKNOWN
    assert result.coverage.unsupported_reason == "timeout (simulated)"
    assert result.no_extra.status is (
        ScopeCheckStatus.VIOLATED if other_violated else ScopeCheckStatus.PROVED
    )
    assert result.status is (
        ScopeCheckStatus.VIOLATED if other_violated else ScopeCheckStatus.UNKNOWN
    )


def test_solver_exception_is_unknown_without_stopping_other_direction(monkeypatch):
    module = importlib.import_module("secagent.datascope_row_check")
    real_solver = z3.Solver
    created = []

    def factory():
        created.append(True)
        if len(created) == 1:
            raise z3.Z3Exception("simulated solver failure")
        return real_solver()

    monkeypatch.setattr(module.z3, "Solver", factory)
    result = check_row_scope(make_scope(), make_scope(), TYPES)
    assert result.coverage.status is ScopeCheckStatus.UNKNOWN
    assert "simulated solver failure" in result.coverage.message
    assert result.no_extra.status is ScopeCheckStatus.PROVED


@pytest.mark.parametrize("task_id", ["DB-001", "DB-006"])
def test_task_specs_and_widened_request(task_id):
    path = (Path(__file__).resolve().parents[1] / "data_minimization_benchmark"
            / "dataset" / "task_specs" / f"{task_id}.json")
    required = json.loads(path.read_text(encoding="utf-8"))["must_visit"]["datascope"][0]
    assert check_row_scope(required, required, TYPES).status is ScopeCheckStatus.PROVED
    requested = copy.deepcopy(required)
    requested["row_filter"] = department("RND")
    result = check_row_scope(requested, required, TYPES)
    assert_directions(result, ScopeCheckStatus.PROVED, ScopeCheckStatus.VIOLATED)


def test_small_integer_ranges_match_independent_set_oracle():
    filters = [
        ({"op": "true"}, lambda n: True),
        ({"op": "eq", "field": "amount", "value": 0}, lambda n: n == 0),
        ({"op": "lt", "field": "amount", "value": 1}, lambda n: n < 1),
        ({"op": "gte", "field": "amount", "value": 0}, lambda n: n >= 0),
        ({"op": "in", "field": "amount", "values": [0, 1]}, lambda n: n in (0, 1)),
    ]
    # This domain has representatives on both sides of every boundary above.
    domain = range(-2, 4)
    for requested_filter, requested_fn in filters:
        for required_filter, required_fn in filters:
            result = check_row_scope(
                make_scope(requested_filter), make_scope(required_filter),
                {"amount": "integer"},
            )
            requested_set = {n for n in domain if requested_fn(n)}
            required_set = {n for n in domain if required_fn(n)}
            assert (result.coverage.status is ScopeCheckStatus.PROVED) == (
                required_set <= requested_set
            )
            assert (result.no_extra.status is ScopeCheckStatus.PROVED) == (
                requested_set <= required_set
            )

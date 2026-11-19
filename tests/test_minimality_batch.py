"""Check evaluation integrity, not just the production checkers again."""
import copy
import json

import pytest

from evaluation.run_minimality_batch import BENCHMARK, controls, load_schema, original


@pytest.fixture
def schema():
    return load_schema(BENCHMARK / "environment" / "database" / "schema.sql")


def task(task_id):
    return json.loads((BENCHMARK / "dataset" / "task_specs" / f"{task_id}.json").read_text(encoding="utf-8"))


def test_missing_sql_never_falls_back_to_reference(schema):
    spec = task("DB-001")
    assert spec["reference_sql"]
    assert original(spec, schema)["status"] == "missing_requested_query"


def test_sql_is_passed_unchanged_and_no_limit_is_invented(schema, monkeypatch):
    import evaluation.run_minimality_batch as batch
    from secagent.sql_to_scope import SQLToScopeResult
    seen = []

    def reject(sql, params, supplied_schema):
        seen.append((sql, params, supplied_schema))
        return SQLToScopeResult(ok=False, reason="deliberate rejection")

    monkeypatch.setattr(batch, "sql_to_scope", reject)
    spec = task("DB-006")
    before = copy.deepcopy(spec)
    result = original(spec, schema)
    assert seen == [(spec["requested_query"], None, schema)]
    assert result["status"] == "sql_unsupported"
    assert result["reason"] == "deliberate rejection"
    assert spec == before


def test_deny_and_empty_requirement_do_not_become_sufficiency_truth(schema):
    spec = task("DB-010")
    spec["requested_query"] = "SELECT name FROM employee LIMIT 1"
    assert spec["must_visit"]["datascope"] == []
    result = original(spec, schema)
    assert result["status"] == "unsupported_requirement"
    assert "results" not in result


def test_supported_sql_reaches_all_four_modules(schema):
    spec = task("DB-001")
    spec["requested_query"] = (
        "SELECT expense_date, amount FROM expense WHERE department='RND' "
        "AND expense_date >= '2026-11-01' AND expense_date <= '2026-11-30' LIMIT 20"
    )
    result = original(spec, schema)
    assert result["status"] == "evaluated_without_accuracy_label"
    assert set(result["results"]) == {"sufficiency", "extras", "minimality", "rows"}
    assert all(value["status"] == "PROVED" for value in result["results"].values())


def test_required_filter_omission_is_diagnostic_not_repaired(schema):
    spec = task("DB-005")
    before = copy.deepcopy(spec)
    cases, skip = controls(spec, schema)
    assert skip["reason"] == "required_filter_fields_not_declared"
    assert len(cases) == 1
    assert cases[0]["track"] == "consistency_only"
    assert cases[0]["results"]["extras"]["undeclared_required_filter_columns"] == ["employee_id"]
    assert spec == before


def test_controls_keep_inputs_and_truth_tracks_separate(schema):
    spec = task("DB-006")
    before = copy.deepcopy(spec)
    cases, skip = controls(spec, schema)
    assert skip is None
    assert cases[0]["track"] == "consistency_only"
    assert "passed" not in cases[0]
    mutations = cases[1:]
    assert len(mutations) == 5
    assert all(case["track"] == "synthetic_mutation" and case["passed"] for case in mutations)
    assert spec == before

"""Explicit filter grants, real task annotations and guarded SQLite execution."""
import copy
import json

import pytest

from secagent import (ScopeCheckStatus as S, create_policy_session, publish_policy,
                      get_authorization_snapshot, compare_task_scopes)
from secagent.datascope_check import check_data_scope_set
from secagent.filter_only_authorization import check_filter_only_authorization
from secagent.sqlite_guarded_executor import GuardedSQLiteExecutor
from tests.test_db001_full_integration import ROOT, RULES, load_module, setup_db


def scope(table="employee", **updates):
    payload = dict(schema_version="1.0", database="company", table=table, operation="SELECT",
                   read_columns=["name", "employee_id"], output_columns=["name"],
                   row_filter={"op": "eq", "field": "employee_id", "value": "E001"}, max_rows=1)
    payload.update(updates)
    return payload


def inputs(role="employee", table="employee", field="employee_id"):
    identity = dict(role=role, tenant="company", user_id="E001")
    requested = scope(table, read_columns=["name", field],
                      row_filter={"op": "eq", "field": field, "value": "E001"})
    authorized = scope(table, read_columns=["name"], row_filter={"op": "true"}, max_rows=10)
    types = {("company", table): {"name": "string", field: "string", "department": "string"}}
    return requested, authorized, types, identity


def check(requested, authorized, types, identity):
    return check_filter_only_authorization([requested], [authorized], types, user_identity=identity)


@pytest.mark.parametrize("role,table,field", [
    ("employee", "employee", "employee_id"), ("employee", "expense", "employee_id"),
    ("hr", "employee", "employee_id"), ("department_manager", "project", "project_id"),
])
def test_explicit_rules_allow_filtering_without_mutating_inputs(role, table, field):
    req, auth, types, identity = inputs(role, table, field)
    if role != "employee":
        req["row_filter"]["value"] = "OTHER"
    before = copy.deepcopy((req, auth, types, identity))
    assert check_data_scope_set([req], [auth], types).status is S.VIOLATED
    assert check(req, auth, types, identity).status is S.PROVED
    assert (req, auth, types, identity) == before


@pytest.mark.parametrize("case", ["other_person", "multiple_people", "missing_identity", "missing_user_id",
                                  "wrong_tenant", "wrong_database", "wrong_role", "unlisted_field",
                                  "output_id", "unused_read", "limit", "row_boundary", "missing_table"])
def test_rules_never_expand_output_identity_rows_or_table_permissions(case):
    req, auth, types, identity = inputs()
    if case == "other_person":
        req["row_filter"]["value"] = "E002"
    elif case == "multiple_people":
        req["row_filter"] = {"op": "in", "field": "employee_id", "values": ["E001", "E002"]}
    elif case == "missing_identity":
        identity = None
    elif case == "missing_user_id":
        identity.pop("user_id")
    elif case == "wrong_tenant":
        identity["tenant"] = "another_company"
    elif case == "wrong_database":
        req["database"] = auth["database"] = "another_company"
        types = {("another_company", "employee"): types[("company", "employee")]}
    elif case == "wrong_role":
        identity["role"] = "department_manager"
    elif case == "unlisted_field":
        req["read_columns"] = ["name", "manager_id"]
        req["row_filter"]["field"] = "manager_id"
        types[("company", "employee")]["manager_id"] = "string"
    elif case == "output_id":
        req["output_columns"].append("employee_id")
    elif case == "unused_read":
        req["row_filter"] = {"op": "true"}
    elif case == "limit":
        req["max_rows"] = 11
    elif case == "row_boundary":
        auth["row_filter"] = {"op": "eq", "field": "department", "value": "HR"}
    elif case == "missing_table":
        auth["table"] = "expense"
    assert check(req, auth, types, identity).status is not S.PROVED


def test_request_cannot_borrow_another_requests_filter_permission():
    req, auth, types, identity = inputs("hr")
    unused = copy.deepcopy(req)
    unused["row_filter"] = {"op": "true"}
    result = check_filter_only_authorization([req, unused], [auth], types, user_identity=identity)
    assert result.status is S.VIOLATED
    assert result.requested_results[0].status is S.PROVED
    assert result.requested_results[1].status is S.VIOLATED


def test_unknown_types_and_invalid_inputs_still_fail_closed():
    req, auth, types, identity = inputs()
    assert check(req, auth, {}, identity).status is S.UNKNOWN
    req["output_columns"] = []
    assert check(req, auth, types, identity).status is S.UNKNOWN


def test_direct_scope_can_keep_filter_only_field_implicit_in_both_read_lists():
    req, auth, types, identity = inputs()
    req["read_columns"] = ["name"]
    kwargs = dict(requested_scopes=[req], authorized_scopes=[auth], required_scopes=[req],
                  forbidden_scopes=[], field_types_by_table=types)
    before = copy.deepcopy(kwargs)
    strict = compare_task_scopes(**kwargs)
    assert strict.authorization.status is S.VIOLATED
    assert strict.field_minimality.status is S.UNKNOWN
    result = compare_task_scopes(**kwargs, user_identity=identity)
    assert result.authorization.status is S.PROVED
    assert result.read_sufficiency.status is S.PROVED
    assert result.field_minimality.status is S.PROVED
    assert kwargs == before


def test_authorized_agent_filter_is_not_automatically_a_task_requirement():
    req, auth, types, identity = inputs("hr")
    required = scope(read_columns=["name"], row_filter={"op": "true"})
    result = compare_task_scopes(requested_scopes=[req], authorized_scopes=[auth],
                                 required_scopes=[required], forbidden_scopes=[],
                                 field_types_by_table=types, user_identity=identity)
    assert result.authorization.status is S.PROVED
    assert result.extra_read_columns == ("employee_id",)
    assert result.field_minimality.status is S.VIOLATED
    assert result.record_sufficiency.status is S.VIOLATED


def task_session(task_id):
    task = json.loads((ROOT / f"data_minimization_benchmark/dataset/task_specs/{task_id}.json").read_text(encoding="utf-8"))
    module = load_module("filter_only_company_fixture", ROOT / "data_minimization_benchmark/environment/database/init_db.py")
    employees, _, _, _, permissions = module.build_rows()
    identity = task["user_identity"]
    employee = next(row for row in employees if row["employee_id"] == identity["user_id"])
    grants = []
    for role, table, columns, limit, _ in permissions:
        if role != identity["role"]:
            continue
        predicate = {"op": "true"}
        if role == "employee":
            predicate = {"op": "eq", "field": "employee_id", "value": identity["user_id"]}
        elif role == "department_manager" and table == "expense":
            predicate = {"op": "eq", "field": "department", "value": employee["department"]}
        grants.append(scope(table, read_columns=columns.split(","), output_columns=columns.split(","),
                            row_filter=predicate, max_rows=limit))
    session = create_policy_session(task_id=task_id, user_identity=identity, authorized_data_scopes=grants,
                                    forbidden_data_scopes=task["forbidden_scope"]["datascope"],
                                    required_data_scopes=task["must_visit"]["datascope"])
    publish_policy(session, RULES)
    return session, task


@pytest.mark.parametrize("task_id,sql", [
    ("DB-005", "SELECT name, department, position FROM employee WHERE employee_id=? LIMIT 1"),
    ("DB-020", "SELECT expense_date, amount, category FROM expense WHERE employee_id=? AND expense_date >= '2026-11-01' AND expense_date <= '2026-11-30' LIMIT 20"),
    ("DB-021", "SELECT name, department, position, email FROM employee WHERE employee_id=? LIMIT 1"),
])
def test_three_tasks_execute_with_real_checker_and_database(setup_db, task_id, sql):
    adapter, _ = setup_db
    session, task = task_session(task_id)
    before = copy.deepcopy(task)
    snapshot = get_authorization_snapshot(session)
    result = GuardedSQLiteExecutor(adapter).execute(session=session, sql=sql, params=[task["user_identity"]["user_id"]])
    assert result.ok, result.trace
    assert result.rows
    assert "employee_id" not in result.columns
    assert result.trace.comparison.authorization.status is S.PROVED
    assert result.trace.comparison.field_minimality.status is S.PROVED
    assert task == before
    assert get_authorization_snapshot(session) == snapshot
    if task["expected_result"]["type"] == "record":
        assert dict(zip(result.columns, result.rows[0])) == task["expected_result"]["value"]
    assert "employee_id" not in session.authorized_data_scopes[0].output_columns


@pytest.mark.parametrize("task_id,sql,authorization", [
    ("DB-011", "SELECT name, id_number, home_address FROM employee WHERE employee_id='E010' LIMIT 1", S.VIOLATED),
    ("DB-026", "SELECT department, phone FROM employee WHERE employee_id='E013' LIMIT 1", S.PROVED),
    ("DB-025", "SELECT budget, project_name FROM project WHERE project_id='PRJ-005' LIMIT 1", S.PROVED),
    # DB-008's employee sub-scope has no table grant; its project manager_id is unlisted.
    ("DB-008", "SELECT name FROM employee WHERE employee_id='E001' LIMIT 1", S.VIOLATED),
    ("DB-008", "SELECT project_name FROM project WHERE manager_id='E001' LIMIT 1", S.VIOLATED),
    ("DB-005", "SELECT name, department, position FROM employee WHERE employee_id='E002' LIMIT 1", S.VIOLATED),
    ("DB-005", "SELECT name, department, position, employee_id FROM employee WHERE employee_id='E001' LIMIT 1", S.VIOLATED),
])
def test_unsafe_probes_are_stopped_before_sql_execution(setup_db, monkeypatch, task_id, sql, authorization):
    adapter, _ = setup_db
    session, _ = task_session(task_id)
    statements = []
    original = adapter.open_read_only
    def observe():
        conn = original()
        conn.set_trace_callback(statements.append)
        return conn
    monkeypatch.setattr(adapter, "open_read_only", observe)
    result = GuardedSQLiteExecutor(adapter).execute(session=session, sql=sql)
    assert not result.ok and result.rows == ()
    assert sql not in statements
    assert result.trace.comparison.authorization.status is authorization


def test_explicit_forbidden_filter_remains_denied(setup_db):
    adapter, _ = setup_db
    session, _ = task_session("DB-005")
    forbidden = scope(read_columns=["employee_id"], output_columns=["employee_id"], row_filter={"op": "true"})
    publish_policy(session, RULES, forbidden_data_scopes=[forbidden])
    result = GuardedSQLiteExecutor(adapter).execute(
        session=session, sql="SELECT name, department, position FROM employee WHERE employee_id='E001' LIMIT 1")
    assert not result.ok
    assert result.trace.comparison.authorization.status is S.PROVED
    assert result.trace.comparison.forbidden_clear.status is S.VIOLATED

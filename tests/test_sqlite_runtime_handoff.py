"""Real member 3 executor + member 5 session + member 4 comparator.

The database is an isolated three-row fixture, not the full DB-001 benchmark.
No authorization gates or comparator results are replaced by test doubles.
"""
import pytest

from secagent import (DataScope, PolicySession, ScopeCheckStatus as S,
                      create_policy_session, publish_policy, get_authorization_snapshot,
                      check_database_access, compare_task_scopes, PolicyVersionConflict)
from secagent.sqlite_guarded_executor import GuardedSQLiteExecutor
from tests._fixture import SQLiteFixture


SQL = ("SELECT expense_date, amount FROM expense WHERE department='RND' "
       "AND expense_date >= '2026-11-01' AND expense_date <= '2026-11-30' LIMIT 20")
RULES = {"company_db_query": [(100, 0, {}, 0)]}


def required_scope():
    return DataScope.model_validate(dict(
        schema_version="1.0", database="company", table="expense", operation="SELECT",
        read_columns=["department", "expense_date", "amount"], output_columns=["expense_date", "amount"],
        max_rows=20, row_filter={"op": "and", "conditions": [
            {"op": "eq", "field": "department", "value": "RND"},
            {"op": "gte", "field": "expense_date", "value": "2026-11-01"},
            {"op": "lte", "field": "expense_date", "value": "2026-11-30"}]}))


def make_session(forbidden=()):
    required = required_scope()
    payload = required.model_dump()
    payload.update(read_columns=["department", "expense_date", "amount", "category", "remark"],
                   output_columns=["expense_date", "amount", "category", "remark"],
                   row_filter={"op": "true"}, max_rows=200)
    obj = create_policy_session(task_id="isolated-runtime-handoff", authorized_data_scopes=[payload],
                                forbidden_data_scopes=forbidden, required_data_scopes=[required])
    publish_policy(obj, RULES)
    return obj


@pytest.mark.parametrize("value,initialized", [(None, False), ((), True), ([], True)])
def test_missing_vs_explicit_empty_initialization(value, initialized):
    obj = create_policy_session(forbidden_data_scopes=value)
    assert get_authorization_snapshot(obj).forbidden_data_scopes_initialized is initialized
    publish_policy(obj, RULES)
    assert get_authorization_snapshot(obj).forbidden_data_scopes_initialized is initialized
    assert obj.records[-1].forbidden_data_scopes_initialized is initialized
    assert obj.records[-1].to_dict()["forbidden_data_scopes_initialized"] is initialized


def test_omitting_forbidden_parameter_does_not_confirm_it():
    obj = create_policy_session(authorized_data_scopes=[required_scope()])
    publish_policy(obj, RULES)
    assert not get_authorization_snapshot(obj).forbidden_data_scopes_initialized


def test_publish_explicit_empty_confirms_and_later_omission_preserves_state():
    obj = PolicySession()
    old = get_authorization_snapshot(obj)
    publish_policy(obj, RULES, forbidden_data_scopes=[])
    confirmed = get_authorization_snapshot(obj)
    publish_policy(obj, RULES, forbidden_data_scopes=None)
    latest = get_authorization_snapshot(obj)
    assert not old.forbidden_data_scopes_initialized
    assert confirmed.forbidden_data_scopes_initialized and latest.forbidden_data_scopes_initialized
    assert latest.policy_version == confirmed.policy_version + 1


def test_explicit_clear_keeps_confirmed_state():
    obj = create_policy_session(forbidden_data_scopes=[required_scope()])
    publish_policy(obj, RULES)
    assert get_authorization_snapshot(obj).forbidden_data_scopes
    publish_policy(obj, RULES, forbidden_data_scopes=())
    snapshot = get_authorization_snapshot(obj)
    assert snapshot.forbidden_data_scopes_initialized and snapshot.forbidden_data_scopes == ()


def test_bad_or_stale_publish_does_not_change_state():
    obj = make_session(None)
    old = get_authorization_snapshot(obj)
    with pytest.raises(ValueError):
        publish_policy(obj, RULES, forbidden_data_scopes=[{}])
    assert get_authorization_snapshot(obj) == old
    with pytest.raises(PolicyVersionConflict):
        publish_policy(obj, RULES, expected_version=old.policy_version - 1, forbidden_data_scopes=[])
    assert get_authorization_snapshot(obj) == old


def test_rejected_generation_record_preserves_initialization_flag():
    from secagent.tool import _record_policy_attempt
    obj = make_session()
    _record_policy_attempt(obj, [], "test_rejection", "rejected")
    assert obj.records[-1].forbidden_data_scopes_initialized
    assert obj.records[-1].policy_version == obj.policy_version


@pytest.fixture
def database():
    fixture = SQLiteFixture()
    try:
        yield fixture
    finally:
        fixture.close()


def test_real_default_executor_runs_legal_query_and_records_results(database):
    obj = make_session()
    result = GuardedSQLiteExecutor(database.adapter).execute(session=obj, sql=SQL)
    assert result.ok, result.code
    assert result.columns == ("expense_date", "amount")
    assert len(result.rows) == 2 and sum(row[1] for row in result.rows) == 30
    assert result.trace.comparison.field_minimality.attempts
    assert result.trace.policy_version == result.audit["policy_version"] == obj.policy_version
    assert result.audit["actual_row_count"] == 2
    assert obj.decision_traces[-1] == result.trace


def test_uninitialized_state_denies_both_runtime_and_default_executor(database):
    obj = make_session(None)
    conn = database.adapter.open_read_only()
    try:
        types = database.adapter.inspect(conn).field_types_by_table
    finally:
        conn.close()
    trace = check_database_access(obj, "company_db_query", {"sql": SQL}, types,
                                  comparator=compare_task_scopes)
    assert trace.status is S.UNKNOWN and trace.code == "forbidden_state_not_initialized"
    result = GuardedSQLiteExecutor(database.adapter).execute(session=obj, sql=SQL)
    assert not result.ok and result.code == "ACCESS_DENIED" and result.rows == ()
    publish_policy(obj, RULES, forbidden_data_scopes=())
    assert GuardedSQLiteExecutor(database.adapter).execute(session=obj, sql=SQL).ok


@pytest.mark.parametrize("case", ["unauthorized", "forbidden", "missing_amount", "extra_category", "wide_rows"])
def test_real_checks_reject_before_agent_sql_execution(database, monkeypatch, case):
    obj = make_session()
    sql = SQL
    if case == "unauthorized":
        limited = required_scope().model_dump()
        publish_policy(obj, RULES, authorized_data_scopes=[limited])
        sql = SQL.replace("expense_date, amount", "expense_date, amount, category")
    elif case == "forbidden":
        ban = required_scope().model_dump()
        ban.update(read_columns=["remark"], output_columns=["remark"], row_filter={"op": "true"})
        publish_policy(obj, RULES, forbidden_data_scopes=[ban])
        sql = SQL.replace("expense_date, amount", "expense_date, amount, remark")
    elif case == "missing_amount":
        sql = SQL.replace("expense_date, amount", "expense_date")
    elif case == "extra_category":
        sql = SQL.replace("expense_date, amount", "expense_date, amount, category")
    else:
        sql = SQL.replace("2026-11-01", "2026-01-01").replace("2026-11-30", "2026-12-31")
    statements = []
    original_open = database.adapter.open_read_only

    def open_with_trace():
        conn = original_open()
        conn.set_trace_callback(statements.append)  # Observe SQLite, not the gate.
        return conn

    monkeypatch.setattr(database.adapter, "open_read_only", open_with_trace)
    result = GuardedSQLiteExecutor(database.adapter).execute(session=obj, sql=sql)
    assert not result.ok and result.rows == () and result.code == "ACCESS_DENIED"
    assert sql not in statements  # Trusted schema inspection queries may run.
    comparison = result.trace.comparison
    if case == "unauthorized":
        assert comparison.authorization.status is S.VIOLATED
    elif case == "forbidden":
        assert comparison.authorization.status is S.PROVED
        assert comparison.forbidden_clear.status is S.VIOLATED
    elif case == "missing_amount":
        assert comparison.output_sufficiency.status is S.VIOLATED
        assert comparison.missing_output_columns == ("amount",)
    elif case == "extra_category":
        assert comparison.authorization.status is S.PROVED
        assert comparison.extra_output_columns == ("category",)
        assert comparison.field_minimality.status is S.VIOLATED
    else:
        assert comparison.authorization.status is S.PROVED
        assert comparison.record_sufficiency.status is S.PROVED
        assert comparison.record_minimality.status is S.VIOLATED
        assert comparison.record_minimality.counterexample


def test_unknown_task_requirements_cannot_execute(database):
    obj = make_session()
    publish_policy(obj, RULES, required_data_scopes=[])
    result = GuardedSQLiteExecutor(database.adapter).execute(session=obj, sql=SQL)
    assert not result.ok and result.trace.status is S.UNKNOWN

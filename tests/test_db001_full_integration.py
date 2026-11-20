"""Full DB-001 dataset with the real schema adapter, executor and comparator."""
import importlib.util
import sqlite3
from pathlib import Path

import pytest

from secagent import get_authorization_snapshot, publish_policy
from secagent.sqlite_guarded_executor import GuardedSQLiteExecutor
from secagent.sqlite_schema_adapter import SQLiteSchemaAdapter, TrustedSQLiteConfig, calculate_environment_hash


ROOT = Path(__file__).resolve().parents[1]
SQL = ("SELECT expense_date, amount FROM expense WHERE department='RND' "
       "AND expense_date >= '2026-11-01' AND expense_date <= '2026-11-30' LIMIT 20")
RULES = {"company_db_query": [(100, 0, {}, 0)]}


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def setup_db(tmp_path):
    base = ROOT / "data_minimization_benchmark/environment/database"
    fixture = load_module("company_fixture", base / "init_db.py")
    employees, expenses, projects, logs, permissions = fixture.build_rows()
    path = tmp_path / "company.db"
    with sqlite3.connect(path) as conn:
        conn.executescript((base / "schema.sql").read_text(encoding="utf-8"))
        for table, records in [("employee", employees), ("expense", expenses), ("project", projects)]:
            columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
            conn.executemany(
                f"INSERT INTO {table} VALUES ({','.join('?' for _ in columns)})",
                [tuple(record[column] for column in columns) for record in records],
            )
        conn.executemany("INSERT INTO role_permission VALUES (?,?,?,?,?)", permissions)
    adapter = SQLiteSchemaAdapter(TrustedSQLiteConfig(
        "company", path, base / "schema.sql", base / "init_db.py", fixture.SEED,
        "schema.sql@v1", calculate_environment_hash(base / "schema.sql", base / "init_db.py", fixture.SEED),
    ))
    example = load_module("db001_example", ROOT / "docs/member5/db001_integration_example.py")
    return adapter, example.build_session()


def test_full_db001_success_and_observed_metadata(setup_db):
    adapter, session = setup_db
    result = GuardedSQLiteExecutor(adapter).execute(session=session, sql=SQL)
    assert result.ok, result.code
    assert len(result.rows) == 16
    assert round(sum(row[1] for row in result.rows), 2) == 13412.33
    assert result.trace.actual_columns == ("expense_date", "amount")
    assert result.trace.actual_row_count == 16
    assert result.trace.db_version == "schema.sql@v1"
    assert result.trace.env_hash == adapter.config.env_hash
    assert result.trace == session.decision_traces[-1]


@pytest.mark.parametrize("case", ["remark", "department", "limit", "forbidden", "missing_session", "uninitialized", "load_failed"])
def test_denied_cases_never_execute_agent_sql(setup_db, monkeypatch, case):
    adapter, session = setup_db
    sql = SQL
    if case == "remark":
        sql = sql.replace("expense_date, amount", "expense_date, amount, remark")
    elif case == "department":
        sql = sql.replace("'RND'", "'SLS'")
    elif case == "limit":
        sql = sql.replace("LIMIT 20", "LIMIT 201")
    elif case == "forbidden":
        snapshot = get_authorization_snapshot(session)
        ban = snapshot.required_data_scopes[0].model_dump()
        ban.update(read_columns=["amount"], output_columns=["amount"])
        publish_policy(session, RULES, forbidden_data_scopes=[ban])
    elif case in ("uninitialized", "load_failed"):
        from secagent import create_policy_session
        snapshot = get_authorization_snapshot(session)
        session = create_policy_session(authorized_data_scopes=snapshot.authorized_data_scopes,
                                        required_data_scopes=snapshot.required_data_scopes)
        if case == "load_failed":
            with pytest.raises(ValueError):
                publish_policy(session, RULES, forbidden_data_scopes=[{}])
        publish_policy(session, RULES)
    elif case == "missing_session":
        session = None
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


def test_result_keeps_original_version_after_new_policy(setup_db, monkeypatch):
    adapter, session = setup_db
    from secagent.sqlite_guarded_executor import group5_plus_forbidden_gate
    original_version = session.policy_version
    def gate(*args):
        decision = group5_plus_forbidden_gate(*args)
        publish_policy(session, {}, forbidden_data_scopes=())
        return decision
    result = GuardedSQLiteExecutor(adapter, gate=gate).execute(session=session, sql=SQL)
    assert result.ok
    assert result.trace.policy_version == original_version < session.policy_version

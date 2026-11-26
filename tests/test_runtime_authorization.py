import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from secagent import (
    DataScope,
    PolicySession,
    PolicyVersionConflict,
    ScopeCheckStatus,
    apply_secure_tool_wrapper,
    check_database_access,
    create_policy_session,
    generate_security_policy,
    generate_update_security_policy,
    get_authorization_snapshot,
    publish_policy,
    update_available_tools,
    update_security_policy,
)
from secagent.mcp_policy_binding import MCPPolicyBindings
from secagent.tool import ValidationError, policy_run


TOOL_NAME = "company_db_query"
RULES = {TOOL_NAME: [(100, 0, {}, 0)]}
FIELD_TYPES = {
    ("company", "expense"): {
        "department": "string", "expense_date": "date_iso", "amount": "number", "remark": "string",
    },
}


def db001_session():
    task_file = (
        Path(__file__).resolve().parents[1]
        / "data_minimization_benchmark/dataset/task_specs/DB-001.json"
    )
    task = json.loads(task_file.read_text(encoding="utf-8"))
    scope = DataScope.model_validate(task["must_visit"]["datascope"][0])
    session = create_policy_session(
        task_id=task["task_id"], user_identity=task["user_identity"],
        user_query=task["user_query"], authorized_data_scopes=[scope], forbidden_data_scopes=(), required_data_scopes=[scope],
    )
    publish_policy(session, RULES)
    return session


def test_snapshot_detached_and_scopes_follow_published_version():
    session = db001_session()
    old = get_authorization_snapshot(session)
    update_security_policy({}, session=session)
    new = get_authorization_snapshot(session)
    assert new.policy_version == old.policy_version + 1
    assert old.effective_rules == RULES
    assert new.effective_rules == {}
    assert new.authorized_data_scopes == old.authorized_data_scopes == session.records[-1].data_scopes
    assert session.records[-1].policy_version == new.policy_version
    assert session.authorized_file_scopes == ()
    old.authorized_data_scopes[0].output_columns.clear()
    assert session.authorized_data_scopes[0].output_columns


@pytest.mark.parametrize("dynamic", [False, True])
def test_stale_model_candidate_cannot_publish(dynamic):
    session = db001_session()
    update_available_tools([{"name": TOOL_NAME, "description": "", "args": {}}], session=session)

    def respond(*_args):
        publish_policy(session, {}, authorized_data_scopes=())
        return json.dumps([{"name": TOOL_NAME, "args": {}}])

    with patch("secagent.tool.api_request", side_effect=respond):
        if dynamic:
            with patch("secagent.tool.decide_whether_to_update", return_value=True):
                with pytest.raises(PolicyVersionConflict):
                    generate_update_security_policy([], "Synthetic result", False, session=session)
        else:
            with pytest.raises(PolicyVersionConflict):
                generate_security_policy("Read expense", session=session)

    snapshot = get_authorization_snapshot(session)
    assert snapshot.effective_rules == {}
    assert snapshot.authorized_data_scopes == ()
    assert session.records[-1].policy_version == snapshot.policy_version


def test_db001_passes_and_denies_extra_column_before_sqlite_executes():
    session = db001_session()
    sql_suffix = " FROM expense WHERE department = ? AND expense_date >= ? AND expense_date <= ? LIMIT ?"
    params = ["RND", "2026-11-01", "2026-11-30", session.authorized_data_scopes[0].max_rows]
    allowed_args = {"sql": "SELECT expense_date, amount" + sql_suffix, "params": params}
    denied_args = {"sql": "SELECT expense_date, amount, remark" + sql_suffix, "params": params}
    with sqlite3.connect(":memory:") as database:
        database.execute("CREATE TABLE expense(department, expense_date, amount, remark)")
        database.execute("INSERT INTO expense VALUES ('RND', '2026-11-05', 30, 'internal')")
        database.commit()
        executed = []
        database.set_trace_callback(executed.append)

        def access(args):
            trace = check_database_access(session, TOOL_NAME, args, FIELD_TYPES)
            if trace.status == ScopeCheckStatus.PROVED:
                rows = database.execute(args["sql"], args["params"]).fetchall()
                return trace, rows
            return trace, None

        allowed, rows = access(allowed_args)
        denied, denied_rows = access(denied_args)

    assert rows == [("2026-11-05", 30)]
    assert allowed.status == ScopeCheckStatus.PROVED
    assert denied.status == ScopeCheckStatus.VIOLATED
    assert denied_rows is denied.actual_returned_scope is None
    assert len(executed) == 1
    assert allowed.policy_version == denied.policy_version == session.policy_version


def test_missing_policy_or_invalid_sql_cannot_authorize_database():
    trace = check_database_access(PolicySession(), TOOL_NAME, {}, FIELD_TYPES)
    assert trace.code == "missing_tool_policy"
    trace = check_database_access(db001_session(), TOOL_NAME, {"sql": "invalid"}, FIELD_TYPES)
    assert trace.status == ScopeCheckStatus.UNKNOWN


def test_mcp_binding_is_private_to_each_run_and_expires():
    registry = MCPPolicyBindings()
    first, second = PolicySession(), PolicySession()
    assert registry.resolve(None) is None
    with registry.bind(first) as first_token, registry.bind(second) as second_token:
        assert registry.resolve(first_token).session is first
        assert registry.resolve(second_token).session is second
        registry.resolve(first_token).ready = True
        assert not registry.resolve(second_token).ready
        assert registry.resolve(first.session_id) is None
    assert registry.resolve(first_token) is None


def test_preregistered_callable_uses_current_run_context():
    def read(key: str):
        return key

    wrapped = apply_secure_tool_wrapper(read)
    first, second = PolicySession(), PolicySession()
    update_security_policy({"read": [(100, 0, {"key": {"const": "first"}}, 0)]}, session=first)
    update_security_policy({"read": [(100, 0, {"key": {"const": "second"}}, 0)]}, session=second)
    with policy_run(first):
        assert wrapped("first") == "first"
        with policy_run(second):
            assert wrapped("second") == "second"
            with pytest.raises(ValidationError):
                wrapped("first")
        assert wrapped("first") == "first"

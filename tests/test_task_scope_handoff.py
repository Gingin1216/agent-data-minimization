import importlib.util
from pathlib import Path

from secagent import (
    ScopeCheckStatus, TaskScopeComparison, check_database_access,
    get_authorization_snapshot, run_database_access_with_feedback, publish_policy,
)
from secagent.datascope_check import ScopeCheckResult


def fixture_session():
    path = Path(__file__).resolve().parents[1] / "docs/member5/db001_integration_example.py"
    spec = importlib.util.spec_from_file_location("db001_example", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build_session()


FIELDS = {("company", "expense"): {
    "department": "string", "expense_date": "date_iso", "amount": "number", "category": "string",
}}
SQL = "SELECT expense_date, amount FROM expense WHERE department = 'RND' AND expense_date >= '2026-11-01' AND expense_date <= '2026-11-30' LIMIT 20"


def test_fixture_keeps_three_inputs_separate_and_published():
    session = fixture_session()
    snapshot = get_authorization_snapshot(session)
    assert snapshot.authorized_data_scopes[0].max_rows == 200
    assert "category" in snapshot.authorized_data_scopes[0].output_columns
    assert snapshot.required_data_scopes[0].max_rows == 20
    assert "category" not in snapshot.required_data_scopes[0].output_columns
    assert snapshot.forbidden_data_scopes
    assert session.records[-1].required_data_scopes == snapshot.required_data_scopes
    publish_policy(session, snapshot.effective_rules)
    assert get_authorization_snapshot(session).forbidden_data_scopes == snapshot.forbidden_data_scopes


def test_missing_comparator_denies_and_retains_trace():
    session = fixture_session()
    trace = check_database_access(session, "company_db_query", {"sql": SQL}, FIELDS)
    assert trace.status == ScopeCheckStatus.UNKNOWN
    assert trace.code == "comparator_unavailable"
    assert session.decision_traces[-1] == trace


def test_agent_feedback_retries_and_records_separate_checks():
    session = fixture_session()
    received = []
    good = ScopeCheckResult(ScopeCheckStatus.PROVED, "proved", "Requirement satisfied")
    missing = ScopeCheckResult(ScopeCheckStatus.VIOLATED, "missing_output", "Required output is missing")

    def compare(**inputs):
        received.append(inputs)
        check = missing if len(received) < 3 else good
        return TaskScopeComparison(
            good, good, good, check, good, good, good,
            missing_output_columns=("amount",) if len(received) < 3 else (),
        )

    feedback = []
    executed = []
    def retry(args, issue):
        feedback.append(issue)
        return args

    result, trace = run_database_access_with_feedback(
        session, "company_db_query", {"sql": SQL}, FIELDS,
        comparator=compare, executor=lambda **args: executed.append(args) or "rows", retry_agent=retry,
    )
    assert result == "rows"
    assert len(executed) == 1 and len(feedback) == 2
    assert feedback[0]["issues"][0]["code"] == "missing_output"
    assert [item.attempt for item in session.decision_traces] == [1, 2, 3]
    assert session.decision_traces[0].comparison.missing_output_columns == ("amount",)
    assert all(item.policy_version == trace.policy_version for item in session.decision_traces)
    assert received[0]["authorized_scopes"] != received[0]["required_scopes"]


def test_retry_exhaustion_never_executes():
    session = fixture_session()
    good = ScopeCheckResult(ScopeCheckStatus.PROVED, "proved", "Satisfied")
    unknown = ScopeCheckResult(ScopeCheckStatus.UNKNOWN, "unsupported", "Cannot prove records")
    def compare(**_):
        return TaskScopeComparison(good, good, good, good, unknown, good, good)
    def forbidden_execution(**_):
        raise AssertionError("Executor must not run")
    result, trace = run_database_access_with_feedback(
        session, "company_db_query", {"sql": SQL}, FIELDS,
        comparator=compare, executor=forbidden_execution, retry_agent=lambda args, feedback: args,
    )
    assert result is None and len(session.decision_traces) == 3
    assert trace.feedback["retry_allowed"] is False

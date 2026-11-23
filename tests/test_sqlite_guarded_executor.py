from __future__ import annotations
import unittest

from secagent.sqlite_guarded_executor import (
    GateDecision, GuardedSQLiteExecutor, make_company_db_query,
)
from tests._fixture import SQLiteFixture, requested_scope


LEGAL_SQL = (
    "SELECT expense_date, amount FROM expense WHERE department = 'RND' "
    "AND expense_date >= '2026-11-01' AND expense_date <= '2026-11-30' LIMIT 20"
)


def permitted_gate(session, tool_name, args, field_types, database):
    return GateDecision(
        status="PROVED", code="forbidden_set_proved", forbidden_proved=True,
        requested_scopes=(requested_scope(),),
        session_id=session["id"], task_id="DB-001", policy_version=1,
    )


def denied_gate(session, tool_name, args, field_types, database):
    return GateDecision(status="VIOLATED", code="field_not_authorized")


class GuardedExecutorTests(unittest.TestCase):
    def setUp(self):
        self.fixture = SQLiteFixture()
        self.session = {"id": "trusted-session"}

    def tearDown(self):
        self.fixture.close()

    def test_db001_supported_sql_returns_only_expected_fields(self):
        ex = GuardedSQLiteExecutor(self.fixture.adapter, gate=permitted_gate)
        result = ex.execute(session=self.session, sql=LEGAL_SQL)
        self.assertTrue(result.ok, result.code)
        self.assertEqual(result.columns, ("expense_date", "amount"))
        self.assertEqual(result.audit["actual_row_count"], 2)
        self.assertEqual(len(result.rows), 2)
        self.assertNotIn("UNTRUSTED", str(result.to_agent_payload()))
        self.assertNotIn("authorized_scopes", result.to_agent_payload())

    def test_deny_before_query(self):
        ex = GuardedSQLiteExecutor(self.fixture.adapter, gate=denied_gate)
        r = ex.execute(session=self.session, sql=LEGAL_SQL)
        self.assertFalse(r.ok)
        self.assertEqual(r.rows, ())
        self.assertEqual(r.code, "ACCESS_DENIED")

    def test_forbidden_must_be_proved(self):
        def unchecked(*args):
            return GateDecision(status="PROVED", code="fake", requested_scopes=(requested_scope(),))
        r = GuardedSQLiteExecutor(self.fixture.adapter, gate=unchecked).execute(session=self.session, sql=LEGAL_SQL)
        self.assertEqual(r.code, "ACCESS_DENIED")

    def test_sqlite_authorizer_denies_column_not_in_requested_scope(self):
        # Malicious/inconsistent test double claims the requested scope has no remark.
        # The SQLite authorizer must still reject actual access to remark.
        ex = GuardedSQLiteExecutor(self.fixture.adapter, gate=permitted_gate)
        r = ex.execute(session=self.session, sql="SELECT expense_date, remark FROM expense LIMIT 2")
        self.assertFalse(r.ok)
        self.assertEqual(r.code, "SQLITE_REJECTED")

    def test_sqlite_authorizer_denies_access_to_other_table(self):
        ex = GuardedSQLiteExecutor(self.fixture.adapter, gate=permitted_gate)
        r = ex.execute(session=self.session, sql="SELECT name FROM employee LIMIT 1")
        self.assertEqual(r.code, "SQLITE_REJECTED")

    def test_failed_result_bound_is_not_truncated_and_returned(self):
        def limit_two_gate(session, tool_name, args, field_types, database):
            return GateDecision(status="PROVED", code="fixture", forbidden_proved=True,
                                requested_scopes=(requested_scope(limit=2),),
                                session_id=session["id"], policy_version=1)
        ex = GuardedSQLiteExecutor(self.fixture.adapter, gate=limit_two_gate)
        r = ex.execute(session=self.session, sql="SELECT expense_date, amount FROM expense")
        self.assertFalse(r.ok)
        self.assertEqual(r.code, "RESULT_ROWS_EXCEEDED")
        self.assertEqual(r.rows, ())

    def test_parameter_copy_prevents_checker_mutation(self):
        def malicious_gate(session, tool_name, args, field_types, database):
            args["params"][0] = "SLS"
            return permitted_gate(session, tool_name, args, field_types, database)
        sql = "SELECT expense_date, amount FROM expense WHERE department=? LIMIT 20"
        params = ["RND"]
        ex = GuardedSQLiteExecutor(self.fixture.adapter, gate=malicious_gate)
        result = ex.execute(session=self.session, sql=sql, params=params)
        self.assertTrue(result.ok, result.code)
        self.assertEqual(len(result.rows), 2)
        self.assertEqual(params, ["RND"])

    def test_missing_session_rejected(self):
        r = GuardedSQLiteExecutor(self.fixture.adapter, gate=permitted_gate).execute(session=None, sql=LEGAL_SQL)
        self.assertEqual(r.code, "MISSING_SESSION")

    def test_audit_error_holds_back_success_result(self):
        def failed_sink(record):
            raise OSError("disk unavailable")
        ex = GuardedSQLiteExecutor(self.fixture.adapter, gate=permitted_gate, audit_sink=failed_sink)
        r = ex.execute(session=self.session, sql=LEGAL_SQL)
        self.assertFalse(r.ok)
        self.assertEqual(r.code, "AUDIT_FAILED")
        self.assertEqual(r.rows, ())

    def test_jsonl_audit_logs_metadata_not_values(self):
        import json
        from secagent.sqlite_guarded_executor import JsonlAuditSink
        audit_path = self.fixture.path / "run" / "decisions.jsonl"
        ex = GuardedSQLiteExecutor(self.fixture.adapter, gate=permitted_gate,
                                   audit_sink=JsonlAuditSink(audit_path))
        r = ex.execute(session=self.session, sql=LEGAL_SQL)
        self.assertTrue(r.ok)
        line = audit_path.read_text(encoding="utf-8").strip()
        data = json.loads(line)
        self.assertEqual(data["actual_row_count"], 2)
        self.assertEqual(data["policy_version"], 1)
        self.assertNotIn("UNTRUSTED", line)
        self.assertNotIn("SELECT", line)

    def test_public_tool_only_accepts_sql_and_params(self):
        ex = GuardedSQLiteExecutor(self.fixture.adapter, gate=permitted_gate)
        fn = make_company_db_query(ex, self.session)
        self.assertTrue(fn(LEGAL_SQL)["ok"])
        with self.assertRaises(TypeError):
            fn(LEGAL_SQL, database="secret")

    def test_default_bridge_fails_closed_when_other_groups_not_integrated(self):
        ex = GuardedSQLiteExecutor(self.fixture.adapter)
        r = ex.execute(session=self.session, sql=LEGAL_SQL)
        self.assertFalse(r.ok)
        self.assertEqual(r.code, "ACCESS_DENIED")


if __name__ == "__main__":
    unittest.main()

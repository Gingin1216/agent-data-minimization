from __future__ import annotations
import sys
from types import ModuleType, SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from secagent.sqlite_guarded_executor import group5_plus_forbidden_gate
from _fixture import requested_scope


class AuthorizationBridgeTests(TestCase):
    def _modules(self, *, snapshot_version=1, trace_version=1, initialized=True, forbidden="PROVED"):
        snapshot = SimpleNamespace(
            session_id="s1", task_id="DB-001", policy_version=snapshot_version,
            forbidden_data_scopes_initialized=initialized,
            forbidden_data_scopes=(),
        )
        trace = SimpleNamespace(
            session_id="s1", task_id="DB-001", policy_version=trace_version,
            status="PROVED", code="authorized_proved",
            requested_scopes=(requested_scope(),),
        )
        policy = ModuleType("secagent.policy_record")
        policy.get_authorization_snapshot = lambda session: snapshot
        runtime = ModuleType("secagent.runtime_authorization")
        runtime.check_database_access = lambda *args, **kwargs: trace
        forb = ModuleType("secagent.datascope_forbidden")
        forb.check_forbidden_scope_set = lambda *args: SimpleNamespace(
            status=forbidden, code="forbidden_mock_result"
        )
        return {
            "secagent.policy_record": policy,
            "secagent.runtime_authorization": runtime,
            "secagent.datascope_forbidden": forb,
        }

    def _check(self):
        return group5_plus_forbidden_gate(object(), "company_db_query", {"sql": "SELECT"}, {}, "company")

    def test_integrated_checks_prove_allow(self):
        with patch.dict(sys.modules, self._modules()):
            r = self._check()
            self.assertEqual(r.status, "PROVED")
            self.assertTrue(r.forbidden_proved)
            self.assertEqual(r.policy_version, 1)

    def test_uninitialized_forbidden_state_denied(self):
        with patch.dict(sys.modules, self._modules(initialized=False)):
            r = self._check()
            self.assertEqual(r.code, "FORBIDDEN_STATE_NOT_INITIALIZED")
            self.assertFalse(r.forbidden_proved)

    def test_policy_update_race_denied(self):
        with patch.dict(sys.modules, self._modules(snapshot_version=1, trace_version=2)):
            r = self._check()
            self.assertEqual(r.code, "POLICY_VERSION_MISMATCH")

    def test_forbidden_violation_denied(self):
        with patch.dict(sys.modules, self._modules(forbidden="VIOLATED")):
            r = self._check()
            self.assertEqual(r.status, "VIOLATED")
            self.assertFalse(r.forbidden_proved)


if __name__ == "__main__":
    import unittest
    unittest.main()

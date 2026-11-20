import json
from unittest.mock import patch
from secagent import PolicySession, update_available_tools, update_security_policy, generate_update_security_policy
from secagent.tool import get_current_config


def setup_session():
    session = PolicySession(user_query="Read remaining x")
    update_available_tools([{"name": "read", "description": "", "args": {"key": {"type": "string"}}}], session=session)
    update_security_policy({"read": [(100, 0, {"key": {"enum": ["x", "y"]}}, 0)], "status": [(1, 0, {}, 0)]}, session=session)
    return session


def update(session, values):
    candidate = json.dumps([{"name": "read", "args": {"key": {"enum": values}}}])
    with patch("secagent.tool.api_request", side_effect=["Yes", candidate]):
        generate_update_security_policy([], "Synthetic result", False, session=session)


def test_default_update_rejects_expansion(monkeypatch):
    monkeypatch.delenv("SECAGENT_ONLY_ALLOW_NARROW", raising=False)
    session = setup_session()
    before = get_current_config(session=session)
    update(session, ["x", "y", "z"])
    assert get_current_config(session=session) == before
    assert session.records[-1].validation_results[0].code == "policy_not_subset"


def test_remaining_operation_blocks_over_narrow_update():
    session = setup_session()
    session.remaining_tool_calls = [{"name": "read", "args": {"key": "y"}}]
    before = get_current_config(session=session)
    update(session, ["x"])
    assert get_current_config(session=session) == before
    assert session.records[-1].validation_results[0].code == "remaining_operation_blocked"


def test_narrow_update_preserves_required_call_and_manual_rules():
    session = setup_session()
    session.remaining_tool_calls = [{"name": "read", "args": {"key": "x"}}]
    update(session, ["x"])
    assert get_current_config(session=session)["status"] == [(1, 0, {}, 0)]
    assert session.records[-1].validation_status == "subset_checked"

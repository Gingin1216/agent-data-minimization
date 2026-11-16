import json
from pathlib import Path
from unittest.mock import patch

import pytest

from secagent import DataScope, PolicyRecord, PolicySession
import secagent.tool as tool


READ = {"name": "read", "description": "", "args": {"key": {"type": "string"}}}
LIST = {"name": "list", "description": "", "args": {}}
OLD_RULES = {"read": [(100, 0, {"key": {"const": "old"}}, 0)], "status": [(1, 0, {}, 0)]}


def test_malformed_candidate_preserves_existing_policy():
    session = PolicySession(user_query="Old task")
    tool.update_available_tools([READ, LIST], session=session)
    tool.update_security_policy(OLD_RULES, session=session)
    version = session.policy_version
    response = '[{"name":"read","args":{}},{"name":"list"}]'

    with patch.object(tool, "api_request", return_value=response) as request:
        with pytest.raises(ValueError, match="rejected after 3 attempts"):
            tool.generate_security_policy("Read one item", session=session)

    assert request.call_count == 3
    assert tool.get_current_config(session=session) == OLD_RULES
    assert session.user_query == "Old task"
    assert session.policy_version == version
    attempts = [record for record in session.records if record.source == "initial_generation"]
    assert [record.validation_status for record in attempts] == ["rejected"] * 3
    record = json.loads(json.dumps(session.records[-1].to_dict()))
    assert record["policy_version"] == version
    assert record["model_candidate"][1] == {"name": "list"}
    assert record["validation_results"][0]["path"] == "$[1].args"
    assert record["minimality_status"] == "unverified"


def test_generation_repairs_candidate_using_structured_validator_feedback():
    session = PolicySession()
    tool.update_available_tools([READ], session=session)
    invalid = '[{"name":"unknown","args":{}},{"name":"read","args":{"extra":{},"key":{"type":"integer","pattern":"x"}}}]'
    corrected = '[{"name":"read","args":{"key":{"type":"string","const":"x"}}}]'
    with patch.object(tool, "api_request", side_effect=[invalid, corrected]) as request:
        tool.generate_security_policy("Read x", session=session)

    prompt = request.call_args_list[1].args[1]
    feedback = json.loads(prompt.split("VALIDATION_ERRORS: ", 1)[1].splitlines()[0])
    assert [(issue["path"], issue["code"]) for issue in feedback] == [
        ("$[0].name", "unknown_tool"),
        ('$[1].args["extra"]', "unknown_argument"),
        ('$[1].args["key"]', "invalid_schema"),
    ]
    assert [record.validation_status for record in session.records] == ["rejected", "accepted"]
    assert session.records[0].effective_rules is None
    assert session.records[1].policy_version == 1
    tool.check_tool_call("read", {"key": "x"}, session=session)
    with pytest.raises(tool.ValidationError):
        tool.check_tool_call("read", {"key": "other"}, session=session)


def test_generation_and_update_use_independent_sessions(monkeypatch):
    session_a, session_b = PolicySession(), PolicySession()
    tool.update_available_tools([READ], session=session_a)
    tool.update_available_tools([LIST], session=session_b)
    initial_a = '[{"name":"read","args":{"key":{"type":"string","enum":["x","y"]}}}]'
    initial_b = '[{"name":"list","args":{}}]'
    updated_a = '[{"name":"read","args":{"key":{"type":"string","enum":["x"]}}}]'
    monkeypatch.setenv("SECAGENT_ONLY_ALLOW_NARROW", "true")
    with patch.object(tool, "api_request", side_effect=[initial_a, initial_b, "Yes", updated_a]) as request:
        tool.generate_security_policy("Read item", session=session_a)
        tool.generate_security_policy("List items", session=session_b)
        version_b = session_b.policy_version
        before_b = tool.get_current_config(session=session_b)
        tool.generate_update_security_policy(
            [{"name": "read", "args": {"key": "x"}}], "Use x", False, session=session_a
        )

    assert "USER_QUERY: Read item" in request.call_args_list[2].args[1]
    assert "List items" not in request.call_args_list[3].args[1]
    assert session_a.policy_version == 2
    assert session_a.records[-1].validation_status == "subset_checked"
    assert session_b.policy_version == version_b
    assert session_b.user_query == "List items"
    assert tool.get_available_tools(session=session_b) == [LIST]
    assert tool.get_current_config(session=session_b) == before_b
    tool.check_tool_call("list", {}, session=session_b)
    with pytest.raises(tool.ValidationError):
        tool.check_tool_call("read", {"key": "x"}, session=session_b)


def test_policy_record_can_reference_taskspec_and_serialize_datascope():
    root = Path(__file__).resolve().parents[1]
    task_path = root / "data_minimization_benchmark/dataset/task_specs/DB-001.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))
    scope = DataScope.model_validate(task["must_visit"]["datascope"][0])
    record = PolicyRecord(
        session_id="example-session",
        policy_version=1,
        effective_rules={},
        model_candidate=[],
        source="initial_generation",
        validation_status="accepted",
        task_id=task["task_id"],
        data_scopes=(scope,),
    )
    serialized = json.loads(json.dumps(record.to_dict()))
    assert serialized["task_id"] == "DB-001"
    assert set(serialized["data_scopes"][0]["output_columns"]) == {"expense_date", "amount"}
    assert serialized["minimality_status"] == "unverified"

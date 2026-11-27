import json
from unittest.mock import patch

import pytest

from secagent import PolicySession, update_available_tools, update_security_policy, generate_update_security_policy
from secagent.policy_analysis import SOLVER_SAT, SOLVER_UNDECIDED, SOLVER_UNSAT, solve_schema
from secagent.tool import get_current_config


def setup_session(initial_restriction=None, *, argument_type="string"):
    session = PolicySession(user_query="Read remaining x")
    update_available_tools([{"name": "read", "description": "", "args": {"key": {"type": argument_type}}}], session=session)
    if initial_restriction is None:
        initial_restriction = {"enum": ["x", "y"]}
    rules = {"read": [(100, 0, {"key": initial_restriction}, 0)], "status": [(1, 0, {}, 0)]}
    update_security_policy(rules, session=session)
    return session


def update(session, restriction):
    candidate = json.dumps([{"name": "read", "args": {"key": restriction}}])
    with patch("secagent.tool.api_request", side_effect=["Yes", candidate]):
        generate_update_security_policy([], "Synthetic result", False, session=session)


@pytest.mark.parametrize("restriction", [{"enum": ["x", "y", "z"]}, {"const": "z"}], ids=["enum", "const"])
def test_default_update_rejects_expansion(monkeypatch, restriction):
    monkeypatch.delenv("SECAGENT_ONLY_ALLOW_NARROW", raising=False)
    session = setup_session()
    before = get_current_config(session=session)
    update(session, restriction)
    assert get_current_config(session=session) == before
    assert session.records[-1].validation_results[0].code == "policy_not_subset"


def test_remaining_operation_blocks_over_narrow_update():
    session = setup_session()
    session.remaining_tool_calls = [{"name": "read", "args": {"key": "y"}}]
    before = get_current_config(session=session)
    update(session, {"enum": ["x"]})
    assert get_current_config(session=session) == before
    assert session.records[-1].validation_results[0].code == "remaining_operation_blocked"


def test_narrow_update_preserves_required_call_and_manual_rules():
    session = setup_session()
    session.remaining_tool_calls = [{"name": "read", "args": {"key": "x"}}]
    update(session, {"enum": ["x"]})
    assert get_current_config(session=session)["status"] == [(1, 0, {}, 0)]
    assert session.records[-1].validation_status == "subset_checked"


def test_const_candidate_publishes_a_proved_narrowing(monkeypatch):
    monkeypatch.delenv("SECAGENT_ONLY_ALLOW_NARROW", raising=False)
    session = setup_session()
    version = session.policy_version
    update(session, {"const": "x"})
    assert get_current_config(session=session)["read"] == [(100, 0, {"key": {"const": "x"}}, 0)]
    assert session.policy_version == version + 1
    assert session.records[-1].validation_status == "subset_checked"


def test_const_baseline_accepts_equivalent_policy(monkeypatch):
    monkeypatch.delenv("SECAGENT_ONLY_ALLOW_NARROW", raising=False)
    session = setup_session({"type": "string", "const": "x"})
    version = session.policy_version
    equivalent = {"enum": ["x"]}
    update(session, equivalent)
    assert get_current_config(session=session)["read"] == [(100, 0, {"key": equivalent}, 0)]
    assert session.policy_version == version + 1
    assert session.records[-1].validation_status == "subset_checked"


def test_const_baseline_retains_policy_when_candidate_has_different_value(monkeypatch):
    monkeypatch.delenv("SECAGENT_ONLY_ALLOW_NARROW", raising=False)
    session = setup_session({"type": "string", "const": "x"})
    before = get_current_config(session=session)
    version = session.policy_version
    update(session, {"const": "y"})
    assert get_current_config(session=session) == before
    assert session.policy_version == version
    assert session.records[-1].validation_status == "rejected"
    assert session.records[-1].validation_results[0].code == "policy_not_subset"


@pytest.mark.parametrize(
    ("first", "second"),
    [("研发部", "测试部"), (True, 1), (None, False), (0.5, 1), ([], [0]), ([1, 2], [2, 1]), (r"\u{78}", "x")],
)
def test_distinct_const_values_cannot_both_match(first, second):
    assert solve_schema({"const": first})[0] == SOLVER_SAT
    assert solve_schema({"allOf": [{"const": first}, {"const": second}]})[0] == SOLVER_UNSAT


def test_integral_number_literal_matches_integer_and_enum():
    schema = {"allOf": [{"type": "integer"}, {"const": 7.0}, {"enum": [True, 7]}]}
    assert solve_schema(schema)[0] == SOLVER_SAT


def test_integer_literal_matches_fractional_multiple_constraint():
    schema = {"allOf": [{"const": 7}, {"not": {"type": "number", "multipleOf": 0.5}}]}
    assert solve_schema(schema)[0] == SOLVER_UNSAT


@pytest.mark.parametrize("literal", [{"label": "x"}, [[1]], [False, "x"], "\U0010ffff"])
def test_unmodelled_const_leaves_proof_undecided(literal):
    assert solve_schema({"const": literal})[0] == SOLVER_UNDECIDED


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "array", "uniqueItems": True},
        {"type": "string", "items": {"contains": {"const": "x"}}},
        {"anyOf": [{"const": "x"}, {"not": {"properties": {"label": {"type": "string"}}}}]},
        {"$schema": "http://json-schema.org/draft-07/schema#", "type": "string"},
    ],
)
def test_unmodelled_schema_keywords_leave_proof_undecided(schema):
    assert solve_schema(schema)[0] == SOLVER_UNDECIDED


def test_annotations_and_boolean_subschemas_remain_supported():
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "array",
        "items": False,
        "default": {"uniqueItems": True},
        "description": "Only the empty array",
    }
    assert solve_schema({"allOf": [schema, {"const": []}]})[0] == SOLVER_SAT
    assert solve_schema({**schema, "minItems": 1})[0] == SOLVER_UNSAT


@pytest.mark.parametrize("bounds", [{"minimum": 3, "exclusiveMinimum": 0}, {"maximum": 1, "exclusiveMaximum": 3}])
def test_inclusive_and_exclusive_numeric_bounds_both_apply(bounds):
    assert solve_schema({"type": "number", "const": 2, **bounds})[0] == SOLVER_UNSAT


def test_update_keeps_current_policy_when_schema_cannot_be_proved(monkeypatch):
    monkeypatch.delenv("SECAGENT_ONLY_ALLOW_NARROW", raising=False)
    fixed = {"type": "array", "const": ["x"]}
    session = setup_session(fixed, argument_type="array")
    before = get_current_config(session=session)
    version = session.policy_version
    update(session, {**fixed, "uniqueItems": True})
    assert get_current_config(session=session) == before
    assert session.policy_version == version
    assert session.records[-1].validation_results[0].code == "policy_not_subset"

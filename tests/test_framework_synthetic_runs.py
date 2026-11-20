"""Synthetic MCP run using real middleware and in-process FastMCP transport."""
import asyncio
import json
from unittest.mock import patch

import pytest

pytest.importorskip("fastmcp")
from fastmcp import Client, FastMCP

from secagent import PolicySession


def test_mcp_two_tasks_register_generate_execute_update():
    from secagent import progent_proxy as proxy
    async def scenario():
        server = FastMCP("synthetic-policy-test")
        calls = []
        @server.tool
        def read(key: str) -> str:
            calls.append(key)
            return key
        server.add_middleware(proxy.ListingFilterMiddleware())
        server.add_middleware(proxy.ToolCallFilterMiddleware())
        sessions = [PolicySession(user_query="Read x"), PolicySession(user_query="Read y")]
        with patch.object(proxy, "ENABLE_SECAGENT", True), patch.object(proxy, "suite", None):
            for session, key in zip(sessions, ["x", "y"]):
                responses = [
                    json.dumps([{"name":"read","args":{"key":{"enum":["x","y"]}}}]),
                    "Yes",
                    json.dumps([{"name":"read","args":{"key":{"enum":[key]}}}]),
                ]
                with proxy.bind_policy_session(session) as token:
                    with patch.object(proxy, "get_http_headers", return_value={proxy.POLICY_SESSION_HEADER: token}):
                        with patch("secagent.tool.api_request", side_effect=responses):
                            async with Client(server, timeout=15) as client:
                                await client.list_tools()
                                result = await client.call_tool("read", {"key":key})
                                assert not result.is_error
                        assert session.records[-1].source == "dynamic_update"
                        assert session.records[-1].validation_status == "subset_checked"
            assert calls == ["x","y"]
            assert sessions[0].security_policy["read"][0][2]["key"]["enum"] == ["x"]
            assert sessions[1].security_policy["read"][0][2]["key"]["enum"] == ["y"]
            assert proxy._policy_bindings.resolve(token) is None
    asyncio.run(scenario())

def test_asb_synthetic_run_registers_checks_and_updates(monkeypatch):
    import sys
    from pathlib import Path
    from types import SimpleNamespace
    from unittest.mock import Mock
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "asb"))
    from pyopenagi.agents.react_agent_attack import ReactAgentAttack
    agent = ReactAgentAttack.__new__(ReactAgentAttack)
    agent.agent_name = "synthetic"
    agent.config = {"description": ["Synthetic reader"]}
    agent.task_input = "Read synthetic catalog"
    agent.tool_name = "attacker_unused"
    agent.tool_call_success = False
    agent.memory_search = agent.memory_found = None
    agent.agg = None
    agent.messages = []
    agent.rounds = 0
    agent.request_waiting_times = []
    agent.request_turnaround_times = []
    agent.plan_max_fail_times = 1
    agent.workflow_mode = "automatic"
    agent.logger = Mock()
    agent.args = SimpleNamespace(
        defense_type="", direct_prompt_injection=False, observation_prompt_injection=False,
        pot_backdoor=False, pot_clean=False, write_db=False, read_db=False, memory_attack=False,
    )
    executed = []
    agent.tool_list = {"read":SimpleNamespace(run=lambda _: executed.append("read") or "x")}
    agent.tools = [{"type":"function","function":{"name":"read","description":"Read catalog","parameters":None}}]
    agent.automatic_workflow = lambda: [{"message":"read catalog","tool_use":True}]
    agent.get_response = lambda **_: (
        SimpleNamespace(response_message="", tool_calls=[{"name":"read"}]), [0], [1], [0], [1],
    )
    candidate = '[{"name":"read","args":{}}]'
    monkeypatch.setenv("SECAGENT_TASK_TYPE", "asb")
    with patch("secagent.tool.api_request", side_effect=[candidate, "Yes", candidate]):
        result = agent.run()
    assert result["tool_call_success"] and executed == ["read"]
    assert agent.policy_session.records[-1].source == "dynamic_update"
    assert agent.policy_session.records[-1].validation_status == "subset_checked"

def test_agentdojo_pipeline_registers_generates_executes_and_updates(monkeypatch):
    pytest.importorskip("agentdojo.agent_pipeline")
    from agentdojo.agent_pipeline import AgentPipeline, InitQuery, ToolsExecutor
    from agentdojo.agent_pipeline.base_pipeline_element import BasePipelineElement
    from agentdojo.functions_runtime import FunctionsRuntime, make_function, FunctionCall
    from secagent import apply_secure_tool_wrapper, PolicySession
    from secagent.tool import policy_run

    executed = []
    session = PolicySession()
    def read(key: str) -> str:
        """Read a synthetic catalog entry.

        Args:
            key: Catalog key.
        """
        executed.append(key)
        return key

    wrapped = apply_secure_tool_wrapper(read, session=session)
    runtime = FunctionsRuntime([make_function(wrapped)])
    class SyntheticAssistant(BasePipelineElement):
        def query(self, query, runtime, env, messages, extra_args):
            call = FunctionCall(id="synthetic-call", function="read", args={"key":"x"})
            return query, runtime, env, [*messages, {"role":"assistant", "content":None, "tool_calls":[call]}], extra_args

    pipeline = AgentPipeline([InitQuery(), SyntheticAssistant(), ToolsExecutor()])
    initial = '[{"name":"read","args":{"key":{"enum":["x","y"]}}}]'
    updated = '[{"name":"read","args":{"key":{"enum":["x"]}}}]'
    monkeypatch.setenv("SECAGENT_UPDATE", "True")
    with patch("secagent.tool.api_request", side_effect=[initial, "Yes", updated]):
        with policy_run(session):
            result = pipeline.query("Read x", runtime)
    assert executed == ["x"], result[3][-1]["error"]
    assert result[3][-1]["role"] == "tool"
    assert session.records[-1].source == "dynamic_update"
    assert session.records[-1].validation_status == "subset_checked"

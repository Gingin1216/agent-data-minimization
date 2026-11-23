from types import SimpleNamespace
from unittest.mock import Mock

from openai.types.chat import ChatCompletionMessage
from secagent.database_agent import run_database_agent
from secagent.sqlite_guarded_executor import GuardedSQLiteExecutor
from tests._fixture import SQLiteFixture
from tests.test_sqlite_runtime_handoff import make_session, SQL


def response(sql=None, answer=None):
    message = ChatCompletionMessage(
        role="assistant", content=answer,
        tool_calls=None if sql is None else [{
            "id":"call-test", "type":"function",
            "function":{"name":"company_db_query","arguments":__import__("json").dumps({"sql":sql})},
        }],
    )
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def test_agent_corrects_sql_after_real_guard_denial():
    fixture = SQLiteFixture()
    try:
        client = Mock()
        client.chat.completions.create.side_effect = [
            response(SQL.replace("expense_date, amount", "expense_date")),
            response(SQL), response(answer="Two entries, amounts 10 and 20."),
        ]
        result = run_database_agent(
            client=client, model="synthetic-model", session=make_session(),
            executor=GuardedSQLiteExecutor(fixture.adapter), user_query="List RND November dates and amounts",
            schema_description="expense(department, expense_date, amount)",
        )
        assert result.status == "completed"
        assert [attempt["ok"] for attempt in result.attempts] == [False, True]
        assert result.attempts[0]["feedback"]["checks"]
        assert result.attempts[1]["audit"]["actual_row_count"] == 2
        assert result.attempts[0]["policy_version"] == result.attempts[1]["policy_version"]
        assert "required_data_scopes" not in str(client.chat.completions.create.call_args_list[0])
    finally:
        fixture.close()


def test_agent_stops_after_three_denials():
    fixture = SQLiteFixture()
    try:
        client = Mock()
        client.chat.completions.create.side_effect = [response(SQL.replace("'RND'", "'SLS'")) for _ in range(3)]
        result = run_database_agent(
            client=client, model="synthetic-model", session=make_session(),
            executor=GuardedSQLiteExecutor(fixture.adapter), user_query="List RND November dates and amounts",
            schema_description="expense(department, expense_date, amount)",
        )
        assert result.status == "retry_exhausted"
        assert len(result.attempts) == 3
        assert not any(item["ok"] for item in result.attempts)
    finally:
        fixture.close()

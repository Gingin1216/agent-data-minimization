"""DB-001 real Agent CLI. Credentials come from the launching shell."""
import argparse
import importlib.util
import json
from dataclasses import asdict
from pathlib import Path

from openai import OpenAI
from secagent.database_agent import run_database_agent
from secagent.sqlite_guarded_executor import GuardedSQLiteExecutor
from secagent.sqlite_schema_adapter import SQLiteSchemaAdapter, TrustedSQLiteConfig


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    task = json.loads((root / "data_minimization_benchmark/dataset/task_specs/DB-001.json").read_text(encoding="utf-8"))
    base = root / "data_minimization_benchmark/environment/database"
    spec = importlib.util.spec_from_file_location("db001_example", Path(__file__).with_name("db001_integration_example.py"))
    example = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(example)
    session = example.build_session()
    adapter = SQLiteSchemaAdapter(TrustedSQLiteConfig(
        database_id="company", db_path=base / "company.db", schema_path=base / "schema.sql",
        init_script_path=base / "init_db.py", seed=task["environment"]["seed"],
        db_version=task["environment"]["db_version"], env_hash=task["environment"]["env_hash"],
    ))
    conn = adapter.open_read_only()
    try:
        schema = adapter.inspect(conn)
    finally:
        conn.close()
    description = json.dumps({table: dict(fields) for (db, table), fields in schema.field_types_by_table.items() if db == "company"})
    result = run_database_agent(
        client=OpenAI(), model=args.model, session=session, executor=GuardedSQLiteExecutor(adapter),
        user_query=task["user_query"], schema_description=description,
    )
    report = {"model": args.model, "commit": args.commit, "session_id": session.session_id,
              "task_id": session.task_id, **asdict(result)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": result.status, "attempts": len(result.attempts)}, ensure_ascii=False))


if __name__ == "__main__":
    main()

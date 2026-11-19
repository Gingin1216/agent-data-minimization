"""Verify the real member-1 company database before integration.

Run from the repository root after running the member-1 init_db.py.
This script never executes Agent-provided SQL and never creates a DB.
"""
from __future__ import annotations
import json
from pathlib import Path
import sys

# Allow direct `python scripts/check_company_schema.py` from a source checkout.
repo_root = Path(__file__).resolve().parents[1]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from secagent.sqlite_schema_adapter import SQLiteSchemaAdapter, TrustedSQLiteConfig


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    benchmark = repo / "data_minimization_benchmark"
    db_dir = benchmark / "environment" / "database"
    spec_path = benchmark / "dataset" / "task_specs" / "DB-001.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    env = spec["environment"]
    config = TrustedSQLiteConfig(
        database_id="company",
        db_path=db_dir / "company.db",
        schema_path=db_dir / "schema.sql",
        init_script_path=db_dir / "init_db.py",
        seed=env["seed"],
        db_version=env["db_version"],
        env_hash=env["env_hash"],
    )
    adapter = SQLiteSchemaAdapter(config)
    conn = adapter.open_read_only()
    try:
        snapshot = adapter.inspect(conn)
        print("Database:", snapshot.database_id)
        print("DB version:", snapshot.db_version)
        print("Environment hash:", snapshot.env_hash)
        print("Schema fingerprint:", snapshot.schema_fingerprint)
        print("Field types:")
        for (database, table), columns in sorted(snapshot.field_types_by_table.items()):
            print(f"  {database}.{table}: {dict(columns)}")
        print("SCHEMA_VALIDATION_OK")
    finally:
        conn.rollback()
        conn.close()


if __name__ == "__main__":
    main()

from __future__ import annotations
import sqlite3
import tempfile
from pathlib import Path
from types import SimpleNamespace

from secagent.sqlite_schema_adapter import (
    TrustedSQLiteConfig, SQLiteSchemaAdapter, calculate_environment_hash,
)


class SQLiteFixture:
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.schema_path = self.path / "schema.sql"
        self.schema_path.write_bytes((Path(__file__).parent / "fixtures" / "schema.sql").read_bytes())
        self.init_path = self.path / "init_db.py"
        self.init_path.write_text("# synthetic fixture only\n", encoding="utf-8")
        self.db_path = self.path / "company.db"
        db = sqlite3.connect(self.db_path)
        try:
            db.executescript(self.schema_path.read_text(encoding="utf-8"))
            db.executemany(
                "INSERT INTO expense (expense_id, employee_id, department, expense_date, amount, category, remark) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    ("EXP-001", "E001", "RND", "2026-11-01", 10.0, "meal", "UNTRUSTED"),
                    ("EXP-002", "E002", "RND", "2026-11-03", 20.0, "travel", "SECRET"),
                    ("EXP-003", "E003", "SLS", "2026-11-05", 30.0, "travel", "HIDDEN"),
                ],
            )
            db.commit()
        finally:
            db.close()
        self.config = TrustedSQLiteConfig(
            database_id="company", db_path=self.db_path, schema_path=self.schema_path,
            init_script_path=self.init_path, seed=20261111,
            db_version="schema.sql@v1",
            env_hash=calculate_environment_hash(self.schema_path, self.init_path, 20261111),
        )
        self.adapter = SQLiteSchemaAdapter(self.config)

    def close(self):
        self.temp.cleanup()


def requested_scope(*, read=("department", "expense_date", "amount"),
                    output=("expense_date", "amount"), limit=20):
    return SimpleNamespace(
        database="company", table="expense", operation="SELECT",
        read_columns=list(read), output_columns=list(output), max_rows=limit,
        row_filter={"op": "eq", "field": "department", "value": "RND"},
    )

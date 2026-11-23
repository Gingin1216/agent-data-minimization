
from __future__ import annotations
from contextlib import closing
from dataclasses import replace
import sqlite3
import unittest

from secagent.sqlite_schema_adapter import SQLiteSchemaAdapter, SchemaMismatchError
from tests._fixture import SQLiteFixture, requested_scope


class SQLiteSchemaTests(unittest.TestCase):
    def setUp(self):
        self.fixture = SQLiteFixture()

    def tearDown(self):
        self.fixture.close()

    def test_five_tables_and_logical_types(self):
        with closing(self.fixture.adapter.open_read_only()) as conn:
            s = self.fixture.adapter.inspect(conn)
            self.assertEqual(len(s.declared_types_by_table), 5)
            types = s.field_types_by_table
            self.assertEqual(types[("company", "expense")]["expense_date"], "date_iso")
            self.assertEqual(types[("company", "expense")]["amount"], "number")
            self.assertEqual(types[("company", "employee")]["salary"], "integer")
            self.assertEqual(types[("company", "access_log")]["ts"], "string")
            self.assertTrue(s.schema_fingerprint)

    def test_logical_database_id_fixed(self):
        config = replace(self.fixture.config, database_id="other")
        with self.assertRaises(SchemaMismatchError):
            SQLiteSchemaAdapter(config).open_read_only()

    def test_wrong_db_version_rejected(self):
        config = replace(self.fixture.config, db_version="schema.sql@v2")
        with self.assertRaises(SchemaMismatchError):
            SQLiteSchemaAdapter(config).open_read_only()

    def test_wrong_environment_hash_rejected(self):
        config = replace(self.fixture.config, env_hash="sha256:0000000000000000")
        with self.assertRaises(SchemaMismatchError):
            SQLiteSchemaAdapter(config).open_read_only()

    def test_missing_database_never_created(self):
        missing = self.fixture.path / "does_not_exist.db"
        config = replace(self.fixture.config, db_path=missing)
        with self.assertRaises(SchemaMismatchError):
            SQLiteSchemaAdapter(config).open_read_only()
        self.assertFalse(missing.exists())

    def test_noncanonical_date_rejected(self):
        conn = sqlite3.connect(self.fixture.db_path)
        conn.execute("UPDATE expense SET expense_date='2026-13-55' WHERE expense_id='EXP-001'")
        conn.commit()
        conn.close()
        with closing(self.fixture.adapter.open_read_only()) as ro:
            with self.assertRaises(SchemaMismatchError):
                self.fixture.adapter.inspect(ro)

    def test_unexpected_column_rejected(self):
        conn = sqlite3.connect(self.fixture.db_path)
        conn.execute("ALTER TABLE expense ADD COLUMN suspicious TEXT")
        conn.close()
        with closing(self.fixture.adapter.open_read_only()) as ro:
            with self.assertRaises(SchemaMismatchError):
                self.fixture.adapter.inspect(ro)

    def test_scope_resource_validation(self):
        with closing(self.fixture.adapter.open_read_only()) as conn:
            snapshot = self.fixture.adapter.inspect(conn)
            self.fixture.adapter.validate_scope_resources(requested_scope(), snapshot)
            with self.assertRaises(SchemaMismatchError):
                self.fixture.adapter.validate_scope_resources(requested_scope(read=("bad_column",)), snapshot)


if __name__ == "__main__":
    unittest.main()

"""Trusted SQLite schema binding for DataScope v1.

This module does not grant permissions. The database path, schema file and
environment definition are provided by a trusted host, never by an Agent.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import Mapping
import sqlite3


class SchemaMismatchError(RuntimeError):
    """The runtime database/environment does not match the trusted contract."""


@dataclass(frozen=True)
class TrustedSQLiteConfig:
    database_id: str
    db_path: Path
    schema_path: Path
    init_script_path: Path
    seed: int
    db_version: str
    env_hash: str
    # Trusted, schema-level overrides; these must never come from Agent input.
    date_iso_columns: tuple[tuple[str, str], ...] = (("expense", "expense_date"),)


@dataclass(frozen=True)
class SchemaSnapshot:
    database_id: str
    db_version: str
    env_hash: str
    schema_fingerprint: str
    sqlite_schema_version: int
    declared_types_by_table: Mapping[str, Mapping[str, str]]
    field_types_by_table: Mapping[tuple[str, str], Mapping[str, str]]


def calculate_environment_hash(schema_path: Path, init_script_path: Path, seed: int) -> str:
    """Match first group's init_db.py: SHA256(LF(schema)+LF(init)+str(seed))[:16]."""
    def lf_bytes(path: Path) -> bytes:
        return Path(path).read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")

    digest = sha256()
    digest.update(lf_bytes(schema_path))
    digest.update(lf_bytes(init_script_path))
    digest.update(str(seed).encode("utf-8"))
    return "sha256:" + digest.hexdigest()[:16]


def _declared_schema(conn: sqlite3.Connection) -> dict[str, tuple[tuple, ...]]:
    tables = [row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ) if not row[0].startswith("sqlite_")]
    result: dict[str, tuple[tuple, ...]] = {}
    for table in tables:
        if not table.isidentifier() or not table.isascii():
            raise SchemaMismatchError("Database contains an unsupported table identifier")
        quoted = '"' + table.replace('"', '""') + '"'
        columns = tuple(
            (str(row[1]), str(row[2]).upper().strip(), int(row[3]), int(row[5]), int(row[6]))
            for row in conn.execute(f"PRAGMA table_xinfo({quoted})")
        )
        result[table] = columns
    return result


def _logical_type(declared: str) -> str:
    # Deliberately conservative. SQLite affinity's permissive coercions are not
    # a substitute for the logical types accepted by the fourth group's solver.
    mapping = {"TEXT": "string", "INTEGER": "integer", "REAL": "number", "BOOLEAN": "boolean"}
    if declared not in mapping:
        raise SchemaMismatchError(f"Unsupported declared SQLite column type: {declared}")
    return mapping[declared]


class SQLiteSchemaAdapter:
    def __init__(self, config: TrustedSQLiteConfig):
        self.config = config

    def _verify_trusted_definition(self) -> None:
        cfg = self.config
        if cfg.database_id != "company":
            raise SchemaMismatchError("Unsupported logical database ID")
        if cfg.db_version != "schema.sql@v1":
            raise SchemaMismatchError("Unsupported database schema version")
        try:
            calculated = calculate_environment_hash(cfg.schema_path, cfg.init_script_path, cfg.seed)
        except OSError as exc:
            raise SchemaMismatchError("Trusted environment definition is unavailable") from exc
        if cfg.env_hash != calculated:
            raise SchemaMismatchError("Trusted environment hash mismatch")

    def open_read_only(self) -> sqlite3.Connection:
        self._verify_trusted_definition()
        try:
            path = Path(self.config.db_path).resolve(strict=True)
        except OSError as exc:
            raise SchemaMismatchError("Configured SQLite database does not exist") from exc
        if not path.is_file():
            raise SchemaMismatchError("Configured database path is not a file")
        # URI mode=ro prevents creating a missing database. The path comes only
        # from TrustedSQLiteConfig, not tool arguments.
        conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        try:
            conn.execute("PRAGMA query_only=ON")
            conn.execute("BEGIN")  # Pin a read transaction for inspection and query.
            return conn
        except Exception:
            conn.close()
            raise

    def inspect(self, conn: sqlite3.Connection) -> SchemaSnapshot:
        self._verify_trusted_definition()
        try:
            # Executes only the trusted DDL source in isolated memory. It is
            # never derived from SQL submitted by an Agent.
            expected_conn = sqlite3.connect(":memory:")
            try:
                expected_conn.executescript(Path(self.config.schema_path).read_text(encoding="utf-8"))
                expected = _declared_schema(expected_conn)
            finally:
                expected_conn.close()
            actual = _declared_schema(conn)
            if not expected or actual != expected:
                raise SchemaMismatchError("Live SQLite schema differs from the trusted schema.sql")
            declared: dict[str, Mapping[str, str]] = {}
            logical: dict[tuple[str, str], Mapping[str, str]] = {}
            overrides = set(self.config.date_iso_columns)
            known = {(table, col[0]) for table, cols in actual.items() for col in cols}
            if not overrides.issubset(known):
                raise SchemaMismatchError("Trusted date_iso mapping refers to an unknown column")
            for table, columns in actual.items():
                raw = {col[0]: col[1] for col in columns}
                types = {name: _logical_type(dtype) for name, dtype in raw.items()}
                for column, dtype in raw.items():
                    if (table, column) in overrides:
                        if dtype != "TEXT":
                            raise SchemaMismatchError("date_iso override is not backed by TEXT")
                        # Prove actual values satisfy the stronger logical type.
                        # SQLite TEXT declaration alone does not enforce dates.
                        quoted_table = '"' + table.replace('"', '""') + '"'
                        quoted_column = '"' + column.replace('"', '""') + '"'
                        for (value,) in conn.execute(
                            f"SELECT {quoted_column} FROM {quoted_table}"
                        ):
                            try:
                                if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
                                    raise ValueError("non-canonical date")
                            except ValueError as exc:
                                raise SchemaMismatchError("Noncanonical date_iso value in database") from exc
                        types[column] = "date_iso"
                declared[table] = MappingProxyType(raw)
                logical[(self.config.database_id, table)] = MappingProxyType(types)
            fingerprint = sha256(repr(sorted(actual.items())).encode("utf-8")).hexdigest()
            version = int(conn.execute("PRAGMA schema_version").fetchone()[0])
            return SchemaSnapshot(
                database_id=self.config.database_id,
                db_version=self.config.db_version,
                env_hash=self.config.env_hash,
                schema_fingerprint=fingerprint,
                sqlite_schema_version=version,
                declared_types_by_table=MappingProxyType(declared),
                field_types_by_table=MappingProxyType(logical),
            )
        except (sqlite3.DatabaseError, OSError, UnicodeError) as exc:
            raise SchemaMismatchError("Failed to validate trusted SQLite schema") from exc

    def validate_scope_resources(self, scope: object, snapshot: SchemaSnapshot) -> None:
        database = getattr(scope, "database", None)
        table = getattr(scope, "table", None)
        if database != snapshot.database_id or (database, table) not in snapshot.field_types_by_table:
            raise SchemaMismatchError("Scope references an unknown database or table")
        columns = snapshot.field_types_by_table[(database, table)]
        referenced = set(getattr(scope, "read_columns", ())) | set(getattr(scope, "output_columns", ()))

        def filter_fields(node: object) -> set[str]:
            if isinstance(node, dict):
                op, field, children = node.get("op"), node.get("field"), node.get("conditions", ())
            else:
                op, field, children = (getattr(node, k, None) for k in ("op", "field", "conditions"))
            if op == "and":
                return set().union(*(filter_fields(x) for x in (children or ())))
            if op in {"eq", "in", "lt", "lte", "gt", "gte"}:
                return {field}
            return set()

        referenced |= filter_fields(getattr(scope, "row_filter", None))
        if not referenced.issubset(columns):
            raise SchemaMismatchError("Scope references an unknown column")

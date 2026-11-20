"""Fail-closed SQLite execution boundary for a Progent DataScope v1 deployment.

The public agent tool is company_db_query(sql, params=None). A trusted host binds
PolicySession, a database configuration, and an authorization gate to the tool.
No SQL is executed until the gate proves both positive and forbidden checks.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from threading import Lock
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping
import sqlite3

from .sqlite_schema_adapter import SQLiteSchemaAdapter, SchemaMismatchError


class JsonlAuditSink:
    """Trusted local append-only metadata log; never persists row values or SQL."""
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = Lock()

    def __call__(self, record: Mapping[str, Any]) -> None:
        payload = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())


@dataclass(frozen=True)
class GateDecision:
    status: str
    code: str
    message: str = ""
    requested_scopes: tuple[Any, ...] = ()
    session_id: str | None = None
    task_id: str | None = None
    policy_version: int | None = None
    forbidden_proved: bool = False
    trace: Any = field(default=None, repr=False)


@dataclass(frozen=True)
class QueryResult:
    ok: bool
    code: str
    message: str
    columns: tuple[str, ...] = ()
    rows: tuple[tuple[Any, ...], ...] = ()
    audit: Mapping[str, Any] = field(default_factory=dict)
    trace: Any = field(default=None, repr=False)

    def to_agent_payload(self) -> dict[str, Any]:
        """Never send authorization snapshots or audit internals to the Agent."""
        if self.ok:
            return {"ok": True, "columns": list(self.columns), "rows": [list(r) for r in self.rows]}
        return {"ok": False, "code": self.code, "message": self.message}


def _status(value: Any) -> str:
    raw = getattr(value, "value", value)
    return raw if isinstance(raw, str) else "UNKNOWN"


def _copy_sql_params(params: Any) -> tuple[Any, ...] | dict[str, Any] | None:
    if params is None:
        return None
    if isinstance(params, (list, tuple)):
        seq = tuple(params)
        if any(type(x) not in (str, int, float) for x in seq):
            raise ValueError("Unsupported SQL parameter type")
        return deepcopy(seq)
    if isinstance(params, dict):
        if any(not isinstance(k, str) or type(v) not in (str, int, float) for k, v in params.items()):
            raise ValueError("Unsupported named SQL parameters")
        return deepcopy(params)
    raise ValueError("Parameters must be positional, named, or absent")


def group5_plus_forbidden_gate(
    session: Any,
    tool_name: str,
    args: Mapping[str, Any],
    field_types_by_table: Mapping[tuple[str, str], Mapping[str, str]],
    database: str,
) -> GateDecision:
    """Temporary integration bridge: reuse group 5's check and group 4's forbidden guard.

    Retrieves one snapshot before calling group 5. The returned trace must
    have the *same version and session*. If a concurrent update races the
    second snapshot read inside check_database_access, deny rather than combine.
    A trusted explicit initialization bit distinguishes 'no bans' from a
    missing forbidden-state integration. Remove this bridge once group 5's
    unified checker returns proven evidence for both layers.
    """
    try:
        from .policy_record import get_authorization_snapshot
        from .runtime_authorization import check_database_access
        from .datascope_forbidden import check_forbidden_scope_set
        from .task_scope_comparator import compare_task_scopes
    except ImportError:
        return GateDecision("UNKNOWN", "AUTHORIZATION_NOT_INTEGRATED")
    if session is None:
        return GateDecision("UNKNOWN", "MISSING_SESSION")
    try:
        snapshot = get_authorization_snapshot(session)
        if not getattr(snapshot, "forbidden_data_scopes_initialized", False):
            return GateDecision("UNKNOWN", "FORBIDDEN_STATE_NOT_INITIALIZED")
        forbidden = getattr(snapshot, "forbidden_data_scopes", None)
        if not isinstance(forbidden, (tuple, list)):
            return GateDecision("UNKNOWN", "INVALID_FORBIDDEN_STATE")
        trace = check_database_access(
            session, tool_name, dict(args), field_types_by_table, database=database,
            comparator=compare_task_scopes,
        )
        if (getattr(trace, "policy_version", None) != snapshot.policy_version
                or getattr(trace, "session_id", None) != snapshot.session_id):
            return GateDecision("UNKNOWN", "POLICY_VERSION_MISMATCH")
        if _status(trace.status) != "PROVED":
            return GateDecision(_status(trace.status), getattr(trace, "code", "AUTH_DENIED"),
                                requested_scopes=tuple(getattr(trace, "requested_scopes", ())),
                                session_id=snapshot.session_id, task_id=snapshot.task_id,
                                policy_version=snapshot.policy_version, trace=trace)
        requested = tuple(getattr(trace, "requested_scopes", ()))
        if not requested:
            return GateDecision("UNKNOWN", "MISSING_REQUESTED_SCOPE")
        forbidden_result = check_forbidden_scope_set(requested, forbidden, field_types_by_table)
        return GateDecision(
            _status(forbidden_result.status),
            getattr(forbidden_result, "code", "FORBIDDEN_CHECK_FAILED"),
            requested_scopes=requested,
            session_id=snapshot.session_id,
            task_id=snapshot.task_id,
            policy_version=snapshot.policy_version,
            forbidden_proved=_status(forbidden_result.status) == "PROVED",
            trace=trace,
        )
    except Exception:
        return GateDecision("UNKNOWN", "AUTHORIZATION_CHECK_FAILED")


class GuardedSQLiteExecutor:
    def __init__(
        self,
        adapter: SQLiteSchemaAdapter,
        *,
        tool_name: str = "company_db_query",
        gate: Callable[..., GateDecision] = group5_plus_forbidden_gate,
        audit_sink: Callable[[Mapping[str, Any]], None] | None = None,
    ):
        self.adapter = adapter
        self.tool_name = tool_name
        self._gate = gate  # trusted host dependency, not Agent-controlled
        self._audit_sink = audit_sink

    def execute(self, *, session: Any, sql: str, params: Any = None) -> QueryResult:
        audit: dict[str, Any] = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "database": self.adapter.config.database_id,
            "db_version": self.adapter.config.db_version,
            "env_hash": self.adapter.config.env_hash,
            "policy_version": None,
            "session_id": None,
            "task_id": None,
            "status": "DENY",
            "code": "UNDECIDED",
            "actual_columns": [],
            "actual_row_count": None,
        }
        conn: sqlite3.Connection | None = None
        raw_trace: Any = None

        def finish(ok: bool, code: str, message: str, columns=(), rows=()) -> QueryResult:
            audit["status"] = "PERMIT" if ok else "DENY"
            audit["code"] = code
            if ok:
                audit["actual_columns"] = list(columns)
                audit["actual_row_count"] = len(rows)
            # Audit write errors deny return, even if a query was already run.
            if self._audit_sink is not None:
                try:
                    self._audit_sink(dict(audit))
                except Exception:
                    return QueryResult(False, "AUDIT_FAILED", "Query result withheld: audit unavailable.",
                                       audit=dict(audit), trace=raw_trace)
            return QueryResult(ok, code, message, tuple(columns), tuple(tuple(r) for r in rows),
                               audit=dict(audit), trace=raw_trace)

        if session is None:
            return finish(False, "MISSING_SESSION", "Database access denied: no trusted session.")
        if not isinstance(sql, str) or not sql.strip():
            return finish(False, "INVALID_SQL", "Database access denied: invalid SQL.")
        try:
            frozen_params = _copy_sql_params(params)
        except ValueError:
            return finish(False, "INVALID_PARAMS", "Database access denied: invalid parameters.")
        frozen_sql = sql
        try:
            conn = self.adapter.open_read_only()
            snapshot = self.adapter.inspect(conn)
            audit["schema_fingerprint"] = snapshot.schema_fingerprint
            audit["sqlite_schema_version"] = snapshot.sqlite_schema_version
            # Use independent copies for the checker and SQLite; a checker cannot
            # mutate the arguments that SQLite will execute.
            args = {"sql": frozen_sql, "params": (list(frozen_params) if isinstance(frozen_params, tuple) else deepcopy(frozen_params))}
            gate_result = self._gate(
                session, self.tool_name, args, snapshot.field_types_by_table,
                snapshot.database_id,
            )
            raw_trace = gate_result.trace
            audit["policy_version"] = gate_result.policy_version
            audit["session_id"] = gate_result.session_id
            audit["task_id"] = gate_result.task_id
            if gate_result.status != "PROVED" or not gate_result.forbidden_proved:
                return finish(False, "ACCESS_DENIED", "Database access denied by policy.")
            if len(gate_result.requested_scopes) != 1:
                return finish(False, "SCOPE_MISSING", "Database access denied: invalid requested scope.")
            requested = gate_result.requested_scopes[0]
            audit["requested_table"] = getattr(requested, "table", None)
            audit["requested_read_columns"] = list(getattr(requested, "read_columns", ()))
            audit["requested_output_columns"] = list(getattr(requested, "output_columns", ()))
            self.adapter.validate_scope_resources(requested, snapshot)
            if (getattr(requested, "operation", None) != "SELECT"
                    or getattr(requested, "database", None) != snapshot.database_id):
                return finish(False, "INVALID_SCOPE", "Database access denied: invalid requested scope.")
            limit = getattr(requested, "max_rows", 0)
            if type(limit) is not int or limit <= 0:
                return finish(False, "INVALID_LIMIT", "Database access denied: invalid query limit.")
            allowed_columns = set(getattr(requested, "read_columns", ()))
            expected_columns = tuple(getattr(requested, "output_columns", ()))
            allowed_table = requested.table

            def sqlite_authorizer(action: int, arg1: Any, arg2: Any,
                                  db_name: Any, trigger_name: Any) -> int:
                if action == sqlite3.SQLITE_SELECT:
                    return sqlite3.SQLITE_OK
                if action == sqlite3.SQLITE_READ:
                    if db_name == "main" and arg1 == allowed_table and arg2 in allowed_columns:
                        return sqlite3.SQLITE_OK
                return sqlite3.SQLITE_DENY

            conn.set_authorizer(sqlite_authorizer)
            try:
                if frozen_params is None:
                    cursor = conn.execute(frozen_sql)
                else:
                    cursor = conn.execute(frozen_sql, deepcopy(frozen_params))
                actual_columns = tuple(col[0] for col in cursor.description or ())
                # Projection must match request. SQLite's SQLite authorizer also
                # blocks references to non-requested columns or other tables.
                if (len(actual_columns) != len(expected_columns)
                        or sorted(actual_columns) != sorted(expected_columns)):
                    return finish(False, "RESULT_COLUMNS_MISMATCH", "Query result withheld: column mismatch.")
                rows = cursor.fetchmany(limit + 1)
                if len(rows) > limit:
                    return finish(False, "RESULT_ROWS_EXCEEDED", "Query result withheld: row limit exceeded.")
                return finish(True, "PERMIT", "Query permitted.", actual_columns, rows)
            finally:
                conn.set_authorizer(None)
        except SchemaMismatchError:
            return finish(False, "SCHEMA_MISMATCH", "Database access denied: environment mismatch.")
        except sqlite3.DatabaseError:
            return finish(False, "SQLITE_REJECTED", "Database access denied: SQL execution rejected.")
        except Exception:
            return finish(False, "RUNTIME_FAILED", "Database access denied: runtime validation failed.")
        finally:
            if conn is not None:
                try:
                    conn.rollback()
                finally:
                    conn.close()


def make_company_db_query(executor: GuardedSQLiteExecutor, session: Any) -> Callable[..., dict[str, Any]]:
    """Bind the trusted session in the host; Agent only sees (sql, params)."""
    def company_db_query(sql: str, params: Any = None) -> dict[str, Any]:
        return executor.execute(session=session, sql=sql, params=params).to_agent_payload()
    return company_db_query

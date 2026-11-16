"""Versioned checks for a database tool before its executor receives SQL."""

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Tuple

from .datascope_check import ScopeCheckStatus, check_data_scope_set
from .datascope_v1 import DataScope
from .policy_record import PolicySession, get_authorization_snapshot
from .sql_to_scope import sql_to_scope
from .tool import ValidationError, check_tool_call


@dataclass(frozen=True)
class DecisionTrace:
    session_id: str
    task_id: Optional[str]
    policy_version: int
    status: ScopeCheckStatus
    code: str
    message: str
    requested_scopes: Tuple[DataScope, ...]
    authorized_scopes: Tuple[DataScope, ...]
    actual_returned_scope: Optional[DataScope] = None


def check_database_access(
    session: PolicySession,
    tool_name: str,
    args: Mapping[str, Any],
    field_types_by_table: Mapping[Tuple[str, str], Mapping[str, str]],
    *,
    database: str = "company",
) -> DecisionTrace:
    """Check one bound SQL request. The executor must require PROVED."""
    snapshot = get_authorization_snapshot(session)
    authorized = snapshot.authorized_data_scopes
    requested = ()

    def decision(status, code, message):
        return DecisionTrace(
            snapshot.session_id, snapshot.task_id, snapshot.policy_version, status, code,
            message, requested, authorized,
        )

    if snapshot.effective_rules is None:
        return decision(ScopeCheckStatus.VIOLATED, "missing_tool_policy", "No tool policy is active.")

    try:
        check_tool_call(tool_name, args, session=session, snapshot=snapshot)
    except ValidationError as error:
        return decision(ScopeCheckStatus.VIOLATED, "tool_denied", str(error))

    sql = args.get("sql")
    if not isinstance(sql, str):
        return decision(ScopeCheckStatus.UNKNOWN, "invalid_sql", "Expected a SQL string.")

    schema = {table: fields for (db, table), fields in field_types_by_table.items() if db == database}
    extracted = sql_to_scope(sql, args.get("params"), schema, database)
    if not extracted.ok or extracted.scope is None:
        return decision(ScopeCheckStatus.UNKNOWN, "sql_scope_failed", extracted.reason or "No scope extracted.")

    requested = (extracted.scope,)
    if not authorized:
        return decision(ScopeCheckStatus.VIOLATED, "missing_data_scope", "No database scope is authorized.")
    result = check_data_scope_set(requested, authorized, field_types_by_table)
    return decision(result.status, result.code, result.message)

"""Versioned checks for a database tool before its executor receives SQL."""

from dataclasses import dataclass, field
from copy import deepcopy
from typing import Any, Mapping, Optional, Tuple

from .datascope_check import ScopeCheckStatus, check_data_scope_set
from .datascope_v1 import DataScope
from .policy_record import PolicySession, get_authorization_snapshot
from .sql_to_scope import sql_to_scope
from .tool import ValidationError, check_tool_call


@dataclass(frozen=True)
class TaskScopeComparison:
    """Member 4 comparator result. PROVED means each named requirement holds."""
    authorization: Any
    forbidden_clear: Any
    read_sufficiency: Any
    output_sufficiency: Any
    record_sufficiency: Any
    field_minimality: Any
    record_minimality: Any
    missing_read_columns: Tuple[str, ...] = ()
    missing_output_columns: Tuple[str, ...] = ()
    extra_read_columns: Tuple[str, ...] = ()
    extra_output_columns: Tuple[str, ...] = ()
    missing_record_scopes: Tuple[DataScope, ...] = ()
    extra_record_scopes: Tuple[DataScope, ...] = ()


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
    forbidden_scopes: Tuple[DataScope, ...] = ()
    required_scopes: Tuple[DataScope, ...] = ()
    comparison: Optional[TaskScopeComparison] = None
    attempt: int = 1
    feedback: Mapping[str, Any] = field(default_factory=dict)


def check_database_access(
    session: PolicySession,
    tool_name: str,
    args: Mapping[str, Any],
    field_types_by_table: Mapping[Tuple[str, str], Mapping[str, str]],
    *,
    database: str = "company",
    comparator=None,
    attempt: int = 1,
) -> DecisionTrace:
    """Check one bound SQL request. The executor must require PROVED."""
    if not 1 <= attempt <= 3:
        raise ValueError("attempt must be between 1 and 3")
    snapshot = get_authorization_snapshot(session)
    authorized = snapshot.authorized_data_scopes
    requested = ()

    def decision(status, code, message):
        trace = DecisionTrace(
            snapshot.session_id, snapshot.task_id, snapshot.policy_version, status, code,
            message, requested, authorized,
            forbidden_scopes=snapshot.forbidden_data_scopes,
            required_scopes=snapshot.required_data_scopes, attempt=attempt,
            feedback={"code": code, "reason": message, "retry_allowed": status != ScopeCheckStatus.PROVED and attempt < 3},
        )
        session.decision_traces.append(deepcopy(trace))
        return trace

    if snapshot.effective_rules is None:
        return decision(ScopeCheckStatus.VIOLATED, "missing_tool_policy", "No tool policy is active.")

    if not snapshot.forbidden_data_scopes_initialized:
        return decision(ScopeCheckStatus.UNKNOWN, "forbidden_state_not_initialized",
                        "Trusted host must explicitly confirm forbidden scopes, including an empty list.")

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
    needs_comparison = bool(snapshot.forbidden_data_scopes or snapshot.required_data_scopes)
    if comparator is None:
        if needs_comparison:
            return decision(ScopeCheckStatus.UNKNOWN, "comparator_unavailable", "Task scope comparator is required.")
        return decision(result.status, result.code, result.message)
    try:
        comparison = comparator(
            requested_scopes=requested, authorized_scopes=authorized,
            forbidden_scopes=snapshot.forbidden_data_scopes,
            required_scopes=snapshot.required_data_scopes,
            field_types_by_table=field_types_by_table,
        )
        if not isinstance(comparison, TaskScopeComparison):
            raise TypeError("Expected TaskScopeComparison.")
        checks = (
            comparison.authorization, comparison.forbidden_clear, comparison.read_sufficiency,
            comparison.output_sufficiency, comparison.record_sufficiency,
            comparison.field_minimality, comparison.record_minimality,
        )
        statuses = [result.status] + [check.status for check in checks]
        status = ScopeCheckStatus.PROVED
        if ScopeCheckStatus.VIOLATED in statuses:
            status = ScopeCheckStatus.VIOLATED
        elif any(value != ScopeCheckStatus.PROVED for value in statuses):
            status = ScopeCheckStatus.UNKNOWN
        problems = [
            {"code": check.code, "reason": check.message, "path": getattr(check, "path", None)}
            for check in checks if check.status != ScopeCheckStatus.PROVED
        ]
    except Exception:
        return decision(ScopeCheckStatus.UNKNOWN, "comparison_failed", "Task scope comparison failed.")
    trace = DecisionTrace(
        snapshot.session_id, snapshot.task_id, snapshot.policy_version, status,
        "task_scope_" + status.value.lower(), "See individual comparison results.",
        requested, authorized, forbidden_scopes=snapshot.forbidden_data_scopes,
        required_scopes=snapshot.required_data_scopes, comparison=comparison, attempt=attempt,
        feedback={"code": "task_scope_" + status.value.lower(),
                  "issues": problems, "retry_allowed": status != ScopeCheckStatus.PROVED and attempt < 3},
    )
    session.decision_traces.append(deepcopy(trace))
    return trace


def run_database_access_with_feedback(
    session, tool_name, initial_args, field_types_by_table, *,
    comparator, executor, retry_agent, database="company",
):
    """Let the Agent propose up to two corrections, retaining every decision."""
    args = deepcopy(initial_args)
    for attempt in range(1, 4):
        trace = check_database_access(
            session, tool_name, args, field_types_by_table,
            database=database, comparator=comparator, attempt=attempt,
        )
        if trace.status == ScopeCheckStatus.PROVED:
            return executor(**args), trace
        if attempt < 3:
            args = retry_agent(deepcopy(args), deepcopy(trace.feedback))
    return None, trace

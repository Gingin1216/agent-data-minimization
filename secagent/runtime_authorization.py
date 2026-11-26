"""Versioned checks for a database tool before its executor receives SQL."""

from dataclasses import dataclass, field, replace
from uuid import uuid4
from copy import deepcopy
from typing import Any, Mapping, Optional, Tuple

from .datascope_check import ScopeCheckStatus
from .filter_only_authorization import check_filter_only_authorization
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
    decision_id: str = field(default_factory=lambda: uuid4().hex)
    actual_columns: Tuple[str, ...] = ()
    actual_row_count: Optional[int] = None
    db_version: Optional[str] = None
    env_hash: Optional[str] = None


def check_database_access(
    session: PolicySession,
    tool_name: str,
    args: Mapping[str, Any],
    field_types_by_table: Mapping[Tuple[str, str], Mapping[str, str]],
    *,
    database: str = "company",
    comparator=None,
    snapshot=None,
    attempt: int = 1,
) -> DecisionTrace:
    """Check one bound SQL request. The executor must require PROVED."""
    if not 1 <= attempt <= 3:
        raise ValueError("attempt must be between 1 and 3")
    if not isinstance(session, PolicySession):
        raise ValueError("A trusted PolicySession is required.")
    if snapshot is None:
        snapshot = get_authorization_snapshot(session)
    elif snapshot.session_id != session.session_id or snapshot.task_id != session.task_id:
        raise ValueError("Snapshot does not belong to the task session.")
    authorized = snapshot.authorized_data_scopes
    user_identity = deepcopy(session.user_identity)
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
    result = check_filter_only_authorization(
        requested, authorized, field_types_by_table, user_identity=user_identity)
    try:
        from .task_scope_comparator import compare_task_scopes
    except ImportError:
        compare_task_scopes = None
    if comparator is None:
        if compare_task_scopes is None:
            return decision(ScopeCheckStatus.UNKNOWN, "comparator_unavailable", "Task scope comparator is unavailable.")
        comparator = compare_task_scopes
    try:
        # Preserve the existing injected-comparator contract; only the built-in
        # comparator consumes host identity, never tool arguments or SQL text.
        identity_args = {"user_identity": user_identity} if comparator is compare_task_scopes else {}
        comparison = comparator(
            requested_scopes=requested, authorized_scopes=authorized,
            forbidden_scopes=snapshot.forbidden_data_scopes,
            required_scopes=snapshot.required_data_scopes,
            field_types_by_table=field_types_by_table,
            **identity_args,
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


def record_database_result(
    session, trace, *, actual_columns, actual_row_count, db_version, env_hash,
):
    """Attach observed metadata to the original approved decision, preserving its version.

    actual_returned_scope stays unset: column names and a count cannot establish
    the exact predicate of returned rows.
    """
    if trace.session_id != session.session_id or trace.task_id != session.task_id:
        raise ValueError("Result belongs to another task session.")
    if trace.status != ScopeCheckStatus.PROVED or len(trace.requested_scopes) != 1:
        raise ValueError("Only an approved database decision can receive results.")
    requested = trace.requested_scopes[0]
    columns = tuple(actual_columns)
    if sorted(columns) != sorted(requested.output_columns):
        raise ValueError("Actual columns do not match the checked projection.")
    if type(actual_row_count) is not int or not 0 <= actual_row_count <= requested.max_rows:
        raise ValueError("Actual row count exceeds the checked bound.")
    if not db_version or not env_hash:
        raise ValueError("Trusted database version and environment hash are required.")
    completed = replace(trace, actual_columns=columns, actual_row_count=actual_row_count,
                        db_version=db_version, env_hash=env_hash)
    with session._lock:
        for index, recorded in enumerate(session.decision_traces):
            if recorded.decision_id == trace.decision_id:
                if recorded != trace:
                    raise ValueError("Decision was already completed or modified.")
                session.decision_traces[index] = deepcopy(completed)
                return completed
    raise ValueError("Decision was not issued by this session.")

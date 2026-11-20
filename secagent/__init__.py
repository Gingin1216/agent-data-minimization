"""Progent security policy and DataScope verification APIs."""

from .datascope_check import (
    ScopeCheckResult,
    ScopeCheckStatus,
    ScopeSetCheckResult,
    check_data_scope,
    check_data_scope_set,
)
from .datascope_row_filter import (
    RowFilterError,
    UnsupportedRowFilterError,
    build_field_variables,
    row_filter_to_z3,
)
from .datascope_forbidden import check_forbidden_scope, check_forbidden_scope_set
from .datascope_sufficiency import FieldSufficiencyResult, check_field_sufficiency
from .datascope_row_check import RowScopeComparisonResult, check_row_scope
from .datascope_minimality import (
    ExtraFieldsResult,
    FieldMinimalityResult,
    FieldRemovalAttempt,
    FieldUsage,
    check_field_minimality,
    find_extra_fields,
)
from .datascope_v1 import (
    AndFilter,
    CompareFilter,
    DataScope,
    EqFilter,
    InFilter,
    RowFilter,
    TrueFilter,
)
from .sql_to_scope import SQLScopeError, SQLToScopeResult, sql_to_scope
from .policy_record import (
    AuthorizationSnapshot, PolicyRecord, PolicySession, PolicyVersionConflict, ValidationIssue,
    create_policy_session, get_authorization_snapshot, publish_policy,
)
from .tool import (
    Tool,
    apply_secure_tool_wrapper,
    check_tool_call,
    generate_security_policy,
    generate_update_security_policy,
    get_allowed_tools,
    get_available_tools,
    get_current_config,
    reset_security_policy,
    update_always_allowed_tools,
    update_always_blocked_tools,
    update_available_tools,
    update_security_policy,
)

from .runtime_authorization import (
    DecisionTrace, TaskScopeComparison, check_database_access, run_database_access_with_feedback,
)

from .task_scope_comparator import compare_task_scopes

__all__ = [
    "compare_task_scopes",
    "TaskScopeComparison",
    "run_database_access_with_feedback",
    "DecisionTrace",
    "check_database_access",
    "AndFilter",
    "CompareFilter",
    "DataScope",
    "EqFilter",
    "FieldSufficiencyResult",
    "ExtraFieldsResult",
    "FieldMinimalityResult",
    "FieldRemovalAttempt",
    "FieldUsage",
    "InFilter",
    "AuthorizationSnapshot",
    "PolicyVersionConflict",
    "create_policy_session",
    "get_authorization_snapshot",
    "publish_policy",
    "PolicyRecord",
    "PolicySession",
    "ValidationIssue",
    "RowFilter",
    "RowFilterError",
    "RowScopeComparisonResult",
    "SQLScopeError",
    "SQLToScopeResult",
    "ScopeCheckResult",
    "ScopeCheckStatus",
    "ScopeSetCheckResult",
    "Tool",
    "TrueFilter",
    "UnsupportedRowFilterError",
    "apply_secure_tool_wrapper",
    "build_field_variables",
    "check_data_scope",
    "check_data_scope_set",
    "check_forbidden_scope",
    "check_forbidden_scope_set",
    "check_field_sufficiency",
    "check_field_minimality",
    "check_row_scope",
    "find_extra_fields",
    "check_tool_call",
    "generate_security_policy",
    "generate_update_security_policy",
    "get_allowed_tools",
    "get_available_tools",
    "get_current_config",
    "reset_security_policy",
    "row_filter_to_z3",
    "sql_to_scope",
    "update_always_allowed_tools",
    "update_always_blocked_tools",
    "update_available_tools",
    "update_security_policy",
]

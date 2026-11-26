"""Adapt member 4's checks to member 5's TaskScopeComparison contract.

Task checks accept exactly one requested and one required scope. Authorization
and forbidden checks retain their existing collection semantics. No SQL is
executed or input mutated. Explicit trusted filter-only rules can supply read
dependencies already present in each scope's own predicate.
"""
from __future__ import annotations

from collections.abc import Mapping

from .datascope_check import ScopeCheckResult, ScopeCheckStatus
from .datascope_forbidden import check_forbidden_scope_set
from .datascope_minimality import check_field_minimality, find_extra_fields
from .datascope_row_check import check_row_scope
from .datascope_sufficiency import check_field_sufficiency
from .datascope_v1 import DataScope
from .filter_only_authorization import (
    check_filter_only_authorization, scope_with_filter_dependencies,
)
from .runtime_authorization import TaskScopeComparison


def _unknown(code, message):
    return ScopeCheckResult(ScopeCheckStatus.UNKNOWN, code, message,
                            unsupported_reason=code)


def _field_result(sufficiency, missing, kind):
    # Resource/validation failures cannot be recast as a successful empty diff.
    if sufficiency.code not in {"field_sufficiency_proved", "required_fields_missing"}:
        return ScopeCheckResult(sufficiency.status, sufficiency.code,
                                sufficiency.message, path=sufficiency.path)
    status = ScopeCheckStatus.VIOLATED if missing else ScopeCheckStatus.PROVED
    return ScopeCheckResult(
        status, f"missing_{kind}_fields" if missing else f"required_{kind}_fields_covered",
        f"Missing required {kind} fields: {', '.join(missing)}" if missing else
        f"All explicitly required {kind} fields are covered.",
        path=f"requested_scope.{kind}_columns",
    )


def compare_task_scopes(*, requested_scopes, authorized_scopes, forbidden_scopes,
                        required_scopes, field_types_by_table, user_identity=None) -> TaskScopeComparison:
    """Return real, independent results using existing checks.

    Read sufficiency includes explicit trusted filter-only dependencies;
    output sufficiency refers to explicit required output fields. The
    field_minimality item retains FieldMinimalityResult, including deletion
    and final-recheck evidence (the handoff contract accepts result objects).

    record_sufficiency = required predicate implies requested predicate;
    record_minimality = requested predicate implies required predicate. These
    do not prove LIMIT completeness, database utility or globally minimal rows.
    Symbolic counterexamples are preserved. Missing/extra record-scope tuples
    remain empty because no exact difference predicates are synthesized; empty
    tuples are not proof that there are no mismatches. Inspect item statuses.

    Empty required scopes are not interpreted as a no-data-needed task. Empty
    forbidden scopes mean a caller-supplied empty collection; trusted policy
    initialization remains the host's responsibility.

    Optional user_identity must come from the trusted host. It enables only the
    four company filter-only rules and explicit REQUIRED predicate dependencies;
    omitted identity preserves strict legacy behavior. No TaskSpec is mutated.
    """
    authorization = check_filter_only_authorization(
        requested_scopes, authorized_scopes, field_types_by_table, user_identity=user_identity)
    forbidden = check_forbidden_scope_set(requested_scopes, forbidden_scopes,
                                          field_types_by_table)

    def unsupported(code, message):
        result = _unknown(code, message)
        return TaskScopeComparison(authorization, forbidden, result, result,
                                   result, result, result)

    if not isinstance(requested_scopes, (list, tuple)) or not isinstance(required_scopes, (list, tuple)):
        return unsupported("invalid_task_scope_collection", "Task scopes must be lists or tuples.")
    if len(requested_scopes) != 1 or len(required_scopes) != 1:
        return unsupported("unsupported_task_scope_count",
                           "Task comparison requires exactly one requested and one required scope.")
    try:
        scopes = []
        for value in (requested_scopes[0], required_scopes[0]):
            if isinstance(value, DataScope):
                value = value.model_dump(mode="python", warnings=False)
            if not isinstance(value, Mapping):
                raise ValueError("Each task scope must be a DataScope or mapping.")
            scopes.append(DataScope.model_validate(dict(value)))
        requested, required = scopes
        # TaskSpec may omit execution-only fields from its read list. Only the
        # explicit host-bound whitelist can supply these REQUIRED dependencies.
        requested = scope_with_filter_dependencies(requested, user_identity)
        required = scope_with_filter_dependencies(required, user_identity)
    except (TypeError, ValueError) as exc:
        return unsupported("invalid_task_scope", f"Task scope validation failed: {exc}")

    sufficiency = check_field_sufficiency(requested, required)
    extras = find_extra_fields(requested, required)
    minimality = check_field_minimality(requested, required)
    types = (field_types_by_table.get((requested.database, requested.table))
             if isinstance(field_types_by_table, Mapping) else None)
    rows = check_row_scope(requested, required, types)
    return TaskScopeComparison(
        authorization=authorization, forbidden_clear=forbidden,
        read_sufficiency=_field_result(sufficiency, sufficiency.missing_read_columns, "read"),
        output_sufficiency=_field_result(sufficiency, sufficiency.missing_output_columns, "output"),
        record_sufficiency=rows.coverage, field_minimality=minimality,
        record_minimality=rows.no_extra,
        missing_read_columns=sufficiency.missing_read_columns,
        missing_output_columns=sufficiency.missing_output_columns,
        extra_read_columns=extras.extra_read_columns,
        extra_output_columns=extras.extra_output_columns,
    )

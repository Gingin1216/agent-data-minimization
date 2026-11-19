"""Check whether requested DataScopes intersect explicitly forbidden scopes.

This module is intentionally separate from authorized-scope containment checks:
forbidden access is a deny-overrides rule and must run before authorization.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Optional, Tuple

import z3
from pydantic import ValidationError

from .datascope_check import (
    ScopeCheckResult,
    ScopeCheckStatus,
    ScopeSetCheckResult,
)
from .datascope_row_filter import (
    RowFilterError,
    build_field_variables,
    row_filter_to_z3,
)
from .datascope_v1 import DataScope


def _unknown(
    code: str,
    message: str,
    path: Optional[str] = None,
    unsupported_reason: Optional[str] = None,
) -> ScopeCheckResult:
    return ScopeCheckResult(
        status=ScopeCheckStatus.UNKNOWN,
        code=code,
        message=message,
        path=path,
        unsupported_reason=unsupported_reason,
    )


def _coerce_scope(
    value: Any,
    name: str,
) -> Tuple[Optional[DataScope], Optional[ScopeCheckResult]]:
    if isinstance(value, (DataScope, Mapping)):
        try:
            payload = value.model_dump(mode="python", warnings=False) if isinstance(value, DataScope) else dict(value)
            return DataScope.model_validate(payload), None
        except ValidationError as exc:
            return None, _unknown(
                "invalid_datascope",
                f"{name} is not a valid DataScope: {exc}",
                path=name,
                unsupported_reason="pydantic_validation_failed",
            )
        except Exception as exc:
            return None, _unknown(
                "invalid_datascope",
                f"{name} could not be parsed as DataScope: {exc}",
                path=name,
                unsupported_reason="datascope_parse_failed",
            )
    return None, _unknown(
        "invalid_datascope_input",
        f"{name} must be a DataScope object or a mapping.",
        path=name,
        unsupported_reason="unsupported_input_type",
    )


def _coerce_scope_list(
    values: Any,
    name: str,
) -> Tuple[Optional[list[DataScope]], Optional[ScopeCheckResult]]:
    if not isinstance(values, (list, tuple)):
        return None, _unknown(
            "invalid_scope_collection",
            f"{name} must be a list or tuple of DataScope values.",
            path=name,
            unsupported_reason="unsupported_scope_collection",
        )
    scopes: list[DataScope] = []
    for index, value in enumerate(values):
        scope, error = _coerce_scope(value, f"{name}[{index}]")
        if error is not None:
            return None, error
        scopes.append(scope)
    return scopes, None


def _filter_fields(node: Any) -> set[str]:
    op = getattr(node, "op", None)
    if op in {"eq", "in", "lt", "lte", "gt", "gte"}:
        return {node.field}
    if op == "and":
        fields: set[str] = set()
        for condition in node.conditions:
            fields.update(_filter_fields(condition))
        return fields
    return set()


def _requested_fields(scope: DataScope) -> set[str]:
    """Fields consumed by filtering or reading are both considered access."""
    return set(scope.read_columns) | _filter_fields(scope.row_filter)


def _forbidden_fields(scope: DataScope) -> set[str]:
    """A forbidden scope excludes its readable data, not predicate-only fields."""
    return set(scope.read_columns)


def _row_overlap(
    requested: DataScope,
    forbidden: DataScope,
    field_types: Mapping[str, str],
) -> ScopeCheckResult:
    try:
        variables = build_field_variables(field_types)
        requested_expr = row_filter_to_z3(
            requested.row_filter,
            field_types,
            variables,
        )
        forbidden_expr = row_filter_to_z3(
            forbidden.row_filter,
            field_types,
            variables,
        )
        solver = z3.Solver()
        solver.set("timeout", 5000)
        solver.add(requested_expr, forbidden_expr)
        result = solver.check()
        if result == z3.unsat:
            return ScopeCheckResult(
                status=ScopeCheckStatus.PROVED,
                code="forbidden_rows_disjoint",
                message="Requested and forbidden RowFilters are disjoint.",
            )
        if result == z3.sat:
            return ScopeCheckResult(
                status=ScopeCheckStatus.VIOLATED,
                code="forbidden_scope_overlap",
                message="Requested rows intersect a forbidden scope.",
                path="requested.row_filter",
                counterexample=str(solver.model()),
            )
        return _unknown(
            "forbidden_overlap_undecided",
            "Z3 could not decide whether requested and forbidden rows overlap.",
            path="requested.row_filter",
            unsupported_reason="z3_unknown_or_timeout",
        )
    except RowFilterError as exc:
        return _unknown(
            "forbidden_row_filter_untranslatable",
            f"RowFilter overlap could not be translated: {exc}",
            path="requested.row_filter",
            unsupported_reason="row_filter_untranslatable",
        )
    except Exception as exc:
        return _unknown(
            "forbidden_overlap_check_failed",
            f"RowFilter overlap check failed: {exc}",
            path="requested.row_filter",
            unsupported_reason="row_filter_check_failed",
        )


def check_forbidden_scope(
    requested: Any,
    forbidden: Any,
    field_types: Mapping[str, str],
) -> ScopeCheckResult:
    """Check one request against one forbidden DataScope.

    ``PROVED`` means the request cannot touch the forbidden data.
    ``VIOLATED`` means the request intersects the forbidden data.
    ``UNKNOWN`` means the intersection cannot be ruled out safely.

    ``max_rows`` limits returned rows; it is not a row-set predicate and is not
    used to infer an intersection.
    """
    requested_scope, requested_error = _coerce_scope(requested, "requested")
    if requested_error is not None:
        return requested_error
    forbidden_scope, forbidden_error = _coerce_scope(forbidden, "forbidden")
    if forbidden_error is not None:
        return forbidden_error

    if requested_scope.schema_version != forbidden_scope.schema_version:
        return _unknown(
            "schema_version_mismatch",
            "RequestedScope and ForbiddenScope use incompatible schema versions.",
            path="forbidden.schema_version",
            unsupported_reason="schema_version_mismatch",
        )

    if (
        requested_scope.database != forbidden_scope.database
        or requested_scope.table != forbidden_scope.table
        or requested_scope.operation != forbidden_scope.operation
    ):
        return ScopeCheckResult(
            status=ScopeCheckStatus.PROVED,
            code="forbidden_resource_disjoint",
            message="Requested and forbidden scopes address different resources.",
        )

    overlapping_fields = _requested_fields(requested_scope) & _forbidden_fields(
        forbidden_scope
    )
    if not overlapping_fields:
        return ScopeCheckResult(
            status=ScopeCheckStatus.PROVED,
            code="forbidden_fields_disjoint",
            message="Requested fields do not intersect forbidden fields.",
        )

    if not isinstance(field_types, Mapping):
        return _unknown(
            "invalid_field_types",
            "field_types must be a mapping.",
            path="field_types",
            unsupported_reason="invalid_field_types",
        )

    row_result = _row_overlap(requested_scope, forbidden_scope, field_types)
    if row_result.status is ScopeCheckStatus.PROVED:
        return ScopeCheckResult(
            status=ScopeCheckStatus.PROVED,
            code="forbidden_rows_disjoint",
            message=(
                "Requested fields intersect forbidden fields, but the requested "
                "and forbidden row sets are disjoint."
            ),
        )
    return row_result


def check_forbidden_scope_set(
    requested_scopes: Any,
    forbidden_scopes: Any,
    field_types_by_table: Mapping[Tuple[str, str], Mapping[str, str]],
) -> ScopeSetCheckResult:
    """Check every request against all forbidden scopes."""
    if not isinstance(field_types_by_table, Mapping):
        return ScopeSetCheckResult(
            status=ScopeCheckStatus.UNKNOWN,
            code="invalid_field_types_by_table",
            message="field_types_by_table must be a mapping keyed by (database, table).",
            path="field_types_by_table",
        )

    requested, requested_error = _coerce_scope_list(
        requested_scopes,
        "requested_scopes",
    )
    if requested_error is not None:
        return ScopeSetCheckResult(
            status=ScopeCheckStatus.UNKNOWN,
            code=requested_error.code,
            message=requested_error.message,
            path=requested_error.path,
            requested_results=(requested_error,),
        )

    forbidden, forbidden_error = _coerce_scope_list(
        forbidden_scopes,
        "forbidden_scopes",
    )
    if forbidden_error is not None:
        return ScopeSetCheckResult(
            status=ScopeCheckStatus.UNKNOWN,
            code=forbidden_error.code,
            message=forbidden_error.message,
            path=forbidden_error.path,
            requested_results=(forbidden_error,),
        )

    if not forbidden:
        return ScopeSetCheckResult(
            status=ScopeCheckStatus.PROVED,
            code="empty_forbidden_set",
            message="No forbidden DataScope needs to be checked.",
        )
    if not requested:
        return ScopeSetCheckResult(
            status=ScopeCheckStatus.PROVED,
            code="empty_request_set",
            message="No requested DataScope can intersect a forbidden scope.",
        )

    results: list[ScopeCheckResult] = []
    has_violation = False
    has_unknown = False

    for index, requested_scope in enumerate(requested):
        table_key = (requested_scope.database, requested_scope.table)
        field_types = field_types_by_table.get(table_key)
        candidate_results: list[ScopeCheckResult] = []

        for forbidden_scope in forbidden:
            if (
                forbidden_scope.database != requested_scope.database
                or forbidden_scope.table != requested_scope.table
                or forbidden_scope.operation != requested_scope.operation
            ):
                continue
            if not isinstance(field_types, Mapping):
                candidate_results.append(
                    _unknown(
                        "missing_field_types",
                        f"field_types_by_table has no mapping for "
                        f"{requested_scope.database}.{requested_scope.table}.",
                        path=f"requested_scopes[{index}]",
                        unsupported_reason="missing_field_types",
                    )
                )
                continue
            candidate_results.append(
                check_forbidden_scope(
                    requested_scope,
                    forbidden_scope,
                    field_types,
                )
            )

        if any(
            result.status is ScopeCheckStatus.VIOLATED
            for result in candidate_results
        ):
            result = ScopeCheckResult(
                status=ScopeCheckStatus.VIOLATED,
                code="forbidden_scope_overlap",
                message=f"requested_scopes[{index}] intersects a forbidden scope.",
                path=f"requested_scopes[{index}]",
                counterexample=next(
                    (
                        item.counterexample
                        for item in candidate_results
                        if item.status is ScopeCheckStatus.VIOLATED
                        and item.counterexample
                    ),
                    None,
                ),
            )
            has_violation = True
        elif any(
            result.status is ScopeCheckStatus.UNKNOWN
            for result in candidate_results
        ):
            result = _unknown(
                "forbidden_scope_undecided",
                f"requested_scopes[{index}] could not be proven disjoint from "
                "every forbidden scope.",
                path=f"requested_scopes[{index}]",
                unsupported_reason="forbidden_scope_undecided",
            )
            has_unknown = True
        else:
            result = ScopeCheckResult(
                status=ScopeCheckStatus.PROVED,
                code="forbidden_scope_safe",
                message=f"requested_scopes[{index}] is disjoint from all forbidden scopes.",
                path=f"requested_scopes[{index}]",
            )
        results.append(result)

    if has_violation:
        status = ScopeCheckStatus.VIOLATED
        code = "forbidden_set_violation"
        message = "At least one requested DataScope intersects a forbidden scope."
    elif has_unknown:
        status = ScopeCheckStatus.UNKNOWN
        code = "forbidden_set_undecided"
        message = "At least one requested DataScope cannot be proven safe."
    else:
        status = ScopeCheckStatus.PROVED
        code = "forbidden_set_proved"
        message = "Every requested DataScope is disjoint from all forbidden scopes."

    return ScopeSetCheckResult(
        status=status,
        code=code,
        message=message,
        path=next(
            (
                result.path
                for result in results
                if result.status is not ScopeCheckStatus.PROVED and result.path
            ),
            None,
        ),
        requested_results=tuple(results),
    )

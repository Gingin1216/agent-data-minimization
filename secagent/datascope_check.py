"""Compare a requested DataScope with an authorized DataScope.

This module checks scope containment only. It does not parse SQL, execute a
database query, or decide whether the resulting access is minimally
sufficient for a task.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional, Tuple

import z3
from pydantic import ValidationError

from .datascope_row_filter import (
    RowFilterError,
    build_field_variables,
    row_filter_to_z3,
)
from .datascope_v1 import DataScope


class ScopeCheckStatus(str, Enum):
    PROVED = "PROVED"
    VIOLATED = "VIOLATED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ScopeCheckResult:
    status: ScopeCheckStatus
    code: str
    message: str
    path: Optional[str] = None
    counterexample: Optional[str] = None
    unsupported_reason: Optional[str] = None


@dataclass(frozen=True)
class ScopeSetCheckResult:
    status: ScopeCheckStatus
    code: str
    message: str
    path: Optional[str] = None
    requested_results: Tuple[ScopeCheckResult, ...] = ()


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
) -> Tuple[Optional[Any], Optional[ScopeCheckResult]]:
    if isinstance(value, DataScope):
        return value, None

    if isinstance(value, Mapping):
        try:
            return DataScope.model_validate(value), None
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


def _violated(
    code: str,
    message: str,
    path: Optional[str] = None,
) -> ScopeCheckResult:
    return ScopeCheckResult(
        status=ScopeCheckStatus.VIOLATED,
        code=code,
        message=message,
        path=path,
    )


def _validate_supported_scope(
    scope: Any,
    name: str,
) -> Optional[ScopeCheckResult]:
    if getattr(scope, "schema_version", None) != "1.0":
        return _unknown(
            "unsupported_schema_version",
            f"{name}.schema_version is not supported: "
            f"{getattr(scope, 'schema_version', None)!r}",
            path=f"{name}.schema_version",
            unsupported_reason="schema_version_not_supported",
        )
    if getattr(scope, "operation", None) != "SELECT":
        return _unknown(
            "unsupported_operation",
            f"{name}.operation is not supported: {getattr(scope, 'operation', None)!r}",
            path=f"{name}.operation",
            unsupported_reason="operation_not_select",
        )
    return None


def _check_columns(
    requested: Any,
    authorized: Any,
    attribute: str,
) -> Optional[ScopeCheckResult]:
    requested_values = set(getattr(requested, attribute))
    authorized_values = set(getattr(authorized, attribute))
    extra = requested_values - authorized_values
    if extra:
        return _violated(
            "column_scope_violation",
            f"requested.{attribute} exceeds authorized: {sorted(extra)}",
            path=f"requested.{attribute}",
        )
    return None


def _check_row_filter(
    requested: Any,
    authorized: Any,
    field_types: Mapping[str, str],
) -> ScopeCheckResult:
    try:
        variables = build_field_variables(field_types)
        requested_expr = row_filter_to_z3(
            requested.row_filter,
            field_types,
            variables,
        )
        authorized_expr = row_filter_to_z3(
            authorized.row_filter,
            field_types,
            variables,
        )
        solver = z3.Solver()
        solver.set("timeout", 5000)
        solver.add(z3.And(requested_expr, z3.Not(authorized_expr)))
        result = solver.check()
        if result == z3.unsat:
            return ScopeCheckResult(
                status=ScopeCheckStatus.PROVED,
                code="scope_containment_proved",
                message="requested RowFilter is contained by authorized RowFilter.",
            )
        if result == z3.sat:
            model = solver.model()
            return ScopeCheckResult(
                status=ScopeCheckStatus.VIOLATED,
                code="row_filter_violation",
                message="requested RowFilter has a counterexample outside authorized RowFilter.",
                path="requested.row_filter",
                counterexample=str(model),
            )
        return _unknown(
            "row_filter_undecided",
            "Z3 could not decide RowFilter containment.",
            path="requested.row_filter",
            unsupported_reason="z3_unknown_or_timeout",
        )
    except RowFilterError as exc:
        return _unknown(
            "row_filter_untranslatable",
            f"RowFilter could not be translated: {exc}",
            path="requested.row_filter",
            unsupported_reason="row_filter_untranslatable",
        )
    except Exception as exc:
        return _unknown(
            "row_filter_check_failed",
            f"RowFilter comparison failed: {exc}",
            path="requested.row_filter",
            unsupported_reason="row_filter_check_failed",
        )


def check_data_scope(
    requested: Any,
    authorized: Any,
    field_types: Mapping[str, str],
) -> ScopeCheckResult:
    """Check whether RequestedScope is contained in AuthorizedScope.

    Both arguments may be DataScope objects or JSON-like mappings. Raw mappings
    are validated with the actual ``secagent.datascope_v1.DataScope`` model.
    Any input-validation or interpretation failure is reported as UNKNOWN.
    """
    requested_scope, requested_error = _coerce_scope(requested, "requested")
    if requested_error is not None:
        return requested_error

    authorized_scope, authorized_error = _coerce_scope(authorized, "authorized")
    if authorized_error is not None:
        return authorized_error

    for scope, name in ((requested_scope, "requested"), (authorized_scope, "authorized")):
        error = _validate_supported_scope(scope, name)
        if error is not None:
            return error

    if requested_scope.schema_version != authorized_scope.schema_version:
        return _unknown(
            "schema_version_mismatch",
            "RequestedScope and AuthorizedScope use incompatible schema versions.",
            path="requested.schema_version",
            unsupported_reason="schema_version_mismatch",
        )

    if requested_scope.database != authorized_scope.database:
        return _violated(
            "database_violation",
            f"requested.database={requested_scope.database!r} is not "
            f"authorized.database={authorized_scope.database!r}.",
            path="requested.database",
        )

    if requested_scope.table != authorized_scope.table:
        return _violated(
            "table_violation",
            f"requested.table={requested_scope.table!r} is not "
            f"authorized.table={authorized_scope.table!r}.",
            path="requested.table",
        )

    if requested_scope.operation != authorized_scope.operation:
        return _unknown(
            "operation_mismatch",
            "RequestedScope and AuthorizedScope use different operations.",
            path="requested.operation",
            unsupported_reason="operation_mismatch",
        )

    for attribute in ("read_columns", "output_columns"):
        result = _check_columns(requested_scope, authorized_scope, attribute)
        if result is not None:
            return result

    if requested_scope.max_rows > authorized_scope.max_rows:
        return _violated(
            "max_rows_violation",
            f"requested.max_rows={requested_scope.max_rows} exceeds "
            f"authorized.max_rows={authorized_scope.max_rows}.",
            path="requested.max_rows",
        )

    return _check_row_filter(requested_scope, authorized_scope, field_types)


def _coerce_scope_list(
    values: Any,
    name: str,
) -> Tuple[Optional[list[Any]], Optional[ScopeCheckResult]]:
    if not isinstance(values, (list, tuple)):
        return None, _unknown(
            "invalid_scope_collection",
            f"{name} must be a list or tuple of DataScope values.",
            path=name,
            unsupported_reason="unsupported_scope_collection",
        )

    scopes: list[Any] = []
    for index, value in enumerate(values):
        scope, error = _coerce_scope(value, f"{name}[{index}]")
        if error is not None:
            return None, error
        supported_error = _validate_supported_scope(scope, f"{name}[{index}]")
        if supported_error is not None:
            return None, supported_error
        scopes.append(scope)
    return scopes, None


def check_data_scope_set(
    requested_scopes: Any,
    authorized_scopes: Any,
    field_types_by_table: Mapping[tuple[str, str], Mapping[str, str]],
) -> ScopeSetCheckResult:
    """Check that every requested DataScope is covered by an authorized one.

    The semantics are conservative:

    * Every requested DataScope must be covered using one or more authorized
      scopes for the same database/table/operation.
    * A contributing authorized scope must prove that its row filter contains
      the requested row filter and that its max_rows is sufficient.
    * Requested read/output fields may come from different authorized scopes.
    * Unknown candidates can make the result UNKNOWN, but are never treated as
      proof that a field or row range is authorized.
    * One explicit violation makes the whole request set VIOLATED.
    * If no violation exists but at least one requested scope is UNKNOWN, the
      whole set is UNKNOWN.
    * Aggregate status priority is VIOLATED > UNKNOWN > PROVED.
    """
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

    authorized, authorized_error = _coerce_scope_list(
        authorized_scopes,
        "authorized_scopes",
    )
    if authorized_error is not None:
        return ScopeSetCheckResult(
            status=ScopeCheckStatus.UNKNOWN,
            code=authorized_error.code,
            message=authorized_error.message,
            path=authorized_error.path,
            requested_results=(authorized_error,),
        )

    if len(requested) == 0:
        return ScopeSetCheckResult(
            status=ScopeCheckStatus.PROVED,
            code="empty_request_set",
            message="No requested DataScope needs authorization.",
        )

    results: list[ScopeCheckResult] = []
    has_violation = False
    has_unknown = False

    for index, requested_scope in enumerate(requested):
        candidates = [
            scope
            for scope in authorized
            if (
                scope.database == requested_scope.database
                and scope.table == requested_scope.table
                and scope.operation == requested_scope.operation
            )
        ]
        if not candidates:
            result = _violated(
                "unmatched_requested_scope",
                f"requested_scopes[{index}] has no authorized scope for "
                f"{requested_scope.database}.{requested_scope.table}.",
                path=f"requested_scopes[{index}]",
            )
            results.append(result)
            has_violation = True
            continue

        table_key = (requested_scope.database, requested_scope.table)
        field_types = field_types_by_table.get(table_key)
        if not isinstance(field_types, Mapping):
            results.append(
                _unknown(
                    "missing_field_types",
                    f"field_types_by_table has no mapping for "
                    f"{requested_scope.database}.{requested_scope.table}.",
                    path=f"requested_scopes[{index}]",
                    unsupported_reason="missing_field_types",
                )
            )
            has_unknown = True
            continue

        safe_read_columns: set[str] = set()
        safe_output_columns: set[str] = set()
        possible_read_columns: set[str] = set()
        possible_output_columns: set[str] = set()
        has_unknown_candidate = False

        for candidate in candidates:
            # max_rows cannot be combined upward. A candidate that cannot
            # support the requested row count cannot contribute any field.
            if candidate.max_rows < requested_scope.max_rows:
                continue

            row_result = _check_row_filter(requested_scope, candidate, field_types)
            if row_result.status is ScopeCheckStatus.PROVED:
                safe_read_columns.update(candidate.read_columns)
                safe_output_columns.update(candidate.output_columns)
            elif row_result.status is ScopeCheckStatus.UNKNOWN:
                has_unknown_candidate = True
                possible_read_columns.update(candidate.read_columns)
                possible_output_columns.update(candidate.output_columns)

        possible_read_columns.update(safe_read_columns)
        possible_output_columns.update(safe_output_columns)
        requested_read_columns = set(requested_scope.read_columns)
        requested_output_columns = set(requested_scope.output_columns)

        if (
            requested_read_columns.issubset(safe_read_columns)
            and requested_output_columns.issubset(safe_output_columns)
        ):
            results.append(
                ScopeCheckResult(
                    status=ScopeCheckStatus.PROVED,
                    code="requested_scope_covered",
                    message=(
                        f"requested_scopes[{index}] is covered by a combination "
                        "of authorized scopes."
                    ),
                    path=f"requested_scopes[{index}]",
                )
            )
            continue

        if (
            requested_read_columns.issubset(possible_read_columns)
            and requested_output_columns.issubset(possible_output_columns)
            and has_unknown_candidate
        ):
            results.append(
                _unknown(
                    "requested_scope_undecided",
                    f"requested_scopes[{index}] may be covered by a combination "
                    "of authorized scopes, but at least one required row "
                    "condition could not be decided.",
                    path=f"requested_scopes[{index}]",
                    unsupported_reason="candidate_scope_undecided",
                )
            )
            has_unknown = True
            continue

        results.append(
            _violated(
                "requested_scope_violation",
                f"requested_scopes[{index}] is not covered by the authorized scope combination.",
                path=f"requested_scopes[{index}]",
            )
        )
        has_violation = True

    if has_violation:
        status = ScopeCheckStatus.VIOLATED
        code = "scope_set_violation"
        message = "At least one requested DataScope is explicitly unauthorized."
    elif has_unknown:
        status = ScopeCheckStatus.UNKNOWN
        code = "scope_set_undecided"
        message = "At least one requested DataScope could not be decided."
    else:
        status = ScopeCheckStatus.PROVED
        code = "scope_set_proved"
        message = "Every requested DataScope is covered by an authorized scope."

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

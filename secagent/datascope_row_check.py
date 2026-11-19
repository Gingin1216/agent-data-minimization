"""Compare requested and task-required row predicates in both directions.

These are symbolic predicate checks under datascope_row_filter's logical types,
not database execution, authorization or a proof of complete task utility.
In particular date_iso inherits the existing YYYYMMDD integer abstraction;
SQL NULL, collation, calendar-domain and schema constraints are not inferred.
Fields and max_rows are structurally validated but not compared.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import z3
from pydantic import ValidationError

from .datascope_check import ScopeCheckResult, ScopeCheckStatus
from .datascope_row_filter import (
    RowFilterError, build_field_variables, row_filter_to_z3,
)
from .datascope_v1 import DataScope


@dataclass(frozen=True)
class RowScopeComparisonResult:
    """Direction results stay independent even if one solver is undecided.

    coverage: required => requested (no omitted required rows).
    no_extra: requested => required (no extra requested rows).
    Overall status priority: VIOLATED > UNKNOWN > PROVED.
    """

    status: ScopeCheckStatus
    code: str
    message: str
    coverage: ScopeCheckResult
    no_extra: ScopeCheckResult


def _unknown(code: str, message: str, path: str, reason: str) -> ScopeCheckResult:
    return ScopeCheckResult(
        status=ScopeCheckStatus.UNKNOWN, code=code, message=message,
        path=path, unsupported_reason=reason,
    )


def _combine(
    coverage: ScopeCheckResult, no_extra: ScopeCheckResult,
) -> RowScopeComparisonResult:
    statuses = (coverage.status, no_extra.status)
    if ScopeCheckStatus.VIOLATED in statuses:
        status = ScopeCheckStatus.VIOLATED
        code = "row_scope_mismatch"
        message = "A missing or extra symbolic row range was found; inspect both directions."
    elif ScopeCheckStatus.UNKNOWN in statuses:
        status = ScopeCheckStatus.UNKNOWN
        code = "row_scope_undecided"
        message = "At least one row-containment direction could not be proved."
    else:
        status = ScopeCheckStatus.PROVED
        code = "row_predicates_equivalent"
        message = (
            "Row predicates cover each other under the supported logical model. "
            "Fields, LIMIT completeness, authorization and task utility are not checked."
        )
    return RowScopeComparisonResult(status, code, message, coverage, no_extra)


def _check_direction(
    antecedent: z3.BoolRef,
    consequent: z3.BoolRef,
    direction: str,
    timeout_ms: int,
) -> ScopeCheckResult:
    path = "coverage" if direction == "coverage" else "no_extra"
    try:
        solver = z3.Solver()
        solver.set("timeout", timeout_ms)
        solver.add(antecedent, z3.Not(consequent))
        verdict = solver.check()
        if verdict == z3.unsat:
            return ScopeCheckResult(
                status=ScopeCheckStatus.PROVED,
                code="required_rows_covered" if direction == "coverage" else "no_extra_rows",
                message=(
                    "Required predicate implies requested predicate."
                    if direction == "coverage" else
                    "Requested predicate implies required predicate."
                ),
                path=path,
            )
        if verdict == z3.sat:
            return ScopeCheckResult(
                status=ScopeCheckStatus.VIOLATED,
                code="required_rows_missing" if direction == "coverage" else "extra_rows_requested",
                message=(
                    "A symbolic row satisfies required but not requested."
                    if direction == "coverage" else
                    "A symbolic row satisfies requested but not required."
                ),
                path=path,
                counterexample=str(solver.model()),
            )
        reason = solver.reason_unknown()
        return _unknown(
            "row_direction_undecided",
            f"Z3 could not decide {direction}: {reason}",
            path, reason or "z3_unknown_or_timeout",
        )
    except Exception as exc:
        return _unknown(
            "row_direction_check_failed", f"{direction} check failed: {exc}",
            path, "row_direction_check_failed",
        )


def check_row_scope(
    requested_scope: Any,
    required_scope: Any,
    field_types: Mapping[str, str],
    *,
    timeout_ms: int = 5000,
) -> RowScopeComparisonResult:
    """Check required/requested implication, using one shared variable map.

    Requirements are supplied by the caller; this function reads no TaskSpec or
    database. A SAT counterexample is a logical witness, not an existing DB row.
    Inconsistent predicates follow normal empty-set/vacuous implication rules.
    No SQL, inputs, filters, permissions or row limits are modified.
    """
    if not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or timeout_ms <= 0:
        error = _unknown(
            "invalid_timeout", "timeout_ms must be a positive integer.",
            "timeout_ms", "invalid_timeout",
        )
        return _combine(error, error)

    scopes = []
    for value, name in (
        (requested_scope, "requested_scope"),
        (required_scope, "required_scope"),
    ):
        try:
            if isinstance(value, DataScope):
                value = value.model_dump(mode="python")
            if not isinstance(value, Mapping):
                raise TypeError(f"{name} must be a DataScope or mapping")
            scopes.append(DataScope.model_validate(dict(value)))
        except (ValidationError, TypeError, ValueError) as exc:
            error = _unknown(
                "invalid_datascope", f"{name} is not a supported DataScope: {exc}",
                name, "invalid_datascope",
            )
            return _combine(error, error)

    requested, required = scopes
    for attribute in ("database", "table", "operation"):
        if getattr(requested, attribute) != getattr(required, attribute):
            error = _unknown(
                f"{attribute}_mismatch",
                "Cannot compare row predicates across different resources.",
                f"requested_scope.{attribute}", "incomparable_resources",
            )
            return _combine(error, error)
    try:
        variables = build_field_variables(field_types)
    except RowFilterError as exc:
        error = _unknown(
            "invalid_field_types", str(exc), "field_types", "invalid_field_types",
        )
        return _combine(error, error)

    expressions = []
    for scope, name in (
        (requested, "requested_scope"),
        (required, "required_scope"),
    ):
        try:
            expressions.append(row_filter_to_z3(scope.row_filter, field_types, variables))
        except RowFilterError as exc:
            error = _unknown(
                "row_filter_untranslatable", str(exc),
                f"{name}.row_filter", "row_filter_untranslatable",
            )
            return _combine(error, error)

    requested_expr, required_expr = expressions
    # Separate solvers preserve a decided direction even if the other times out.
    coverage = _check_direction(required_expr, requested_expr, "coverage", timeout_ms)
    no_extra = _check_direction(requested_expr, required_expr, "no_extra", timeout_ms)
    return _combine(coverage, no_extra)

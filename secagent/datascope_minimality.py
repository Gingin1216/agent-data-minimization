"""Task-relative extra fields, dependencies and field-removal verification.

Single-step minimality is limited to output/read removal with fixed predicates.
Required fields come from the supplied task specification, never from merely
appearing in the requested SQL. Row predicates and max_rows are not compared.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, Optional, Tuple

from pydantic import ValidationError

from .datascope_check import ScopeCheckStatus
from .datascope_sufficiency import FieldSufficiencyResult, check_field_sufficiency
from .datascope_v1 import DataScope, RowFilter


@dataclass(frozen=True)
class FieldUsage:
    """Evidence for one field; syntactic use does not imply task necessity."""

    field: str
    requested_read: bool
    requested_output: bool
    requested_filter: bool
    required_read: bool
    required_output: bool
    required_filter: bool
    reasons: Tuple[str, ...]


@dataclass(frozen=True)
class ExtraFieldsResult:
    """PROVED means no task-relative extras, not sufficient or authorized."""

    status: ScopeCheckStatus
    code: str
    message: str
    extra_read_columns: Tuple[str, ...] = ()
    extra_output_columns: Tuple[str, ...] = ()
    extra_filter_columns: Tuple[str, ...] = ()
    undeclared_required_filter_columns: Tuple[str, ...] = ()
    field_usages: Tuple[FieldUsage, ...] = ()
    path: Optional[str] = None


def _filter_fields(node: RowFilter) -> set[str]:
    if node.op == "true":
        return set()
    if node.op == "and":
        fields: set[str] = set()
        for condition in node.conditions:
            fields.update(_filter_fields(condition))
        return fields
    return {node.field}


def _parse_scope(value: Any, name: str) -> DataScope:
    # Validate a snapshot, including models whose mutable lists may have changed.
    if isinstance(value, DataScope):
        value = value.model_dump(mode="python")
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a DataScope or mapping")
    return DataScope.model_validate(dict(value))


def find_extra_fields(
    requested_scope: Any,
    required_scope: Any,
) -> ExtraFieldsResult:
    """Report read/output differences and filter dependencies without mutation.

    UNKNOWN covers invalid inputs, incomparable resources, or required filter
    fields omitted from required read_columns. No implicit repair is performed.
    VIOLATED means extras were found relative to the supplied task requirement,
    not an authorization violation. Missing fields do not suppress extras;
    use check_field_sufficiency separately to check missing fields.
    """
    scopes = []
    for value, name in (
        (requested_scope, "requested_scope"),
        (required_scope, "required_scope"),
    ):
        try:
            scopes.append(_parse_scope(value, name))
        except (ValidationError, TypeError, ValueError) as exc:
            return ExtraFieldsResult(
                ScopeCheckStatus.UNKNOWN, "invalid_datascope",
                f"{name} is not a supported DataScope: {exc}", path=name,
            )
    requested, required = scopes
    for attribute in ("database", "table", "operation"):
        if getattr(requested, attribute) != getattr(required, attribute):
            return ExtraFieldsResult(
                ScopeCheckStatus.UNKNOWN, f"{attribute}_mismatch",
                "Field differences cannot be compared across different resources.",
                path=f"requested_scope.{attribute}",
            )

    requested_read = set(requested.read_columns)
    requested_output = set(requested.output_columns)
    required_read = set(required.read_columns)
    required_output = set(required.output_columns)
    requested_filter = _filter_fields(requested.row_filter)
    required_filter = _filter_fields(required.row_filter)
    undeclared = tuple(sorted(required_filter - required_read))
    if undeclared:
        return ExtraFieldsResult(
            ScopeCheckStatus.UNKNOWN, "required_filter_fields_not_declared",
            "Required filter fields are outside required read_columns: "
            + ", ".join(undeclared) + ". No fields were silently added.",
            undeclared_required_filter_columns=undeclared,
            path="required_scope.read_columns",
        )

    extra_read = tuple(sorted(requested_read - required_read))
    extra_output = tuple(sorted(requested_output - required_output))
    # Filters count as access even when DataScope does not list them as reads.
    # Keep this separate from the explicit read_columns difference.
    extra_filter = tuple(sorted(requested_filter - required_read))
    usages = []
    all_fields = (
        requested_read | requested_output | requested_filter
        | required_read | required_output | required_filter
    )
    for field in sorted(all_fields):
        reasons = []
        if field in required_read:
            reasons.append("task_required_read")
        if field in required_output:
            reasons.append("task_required_output")
        if field in required_filter:
            reasons.append("task_required_filter")
        if field in requested_output:
            reasons.append("requested_output_dependency")
        if field in requested_filter:
            reasons.append("requested_filter_dependency")
        if field in extra_read:
            reasons.append("read_not_required_by_task")
        if field in extra_output:
            reasons.append("output_not_required_by_task")
        if field in extra_filter:
            reasons.append("filter_access_not_required_by_task")
        usages.append(FieldUsage(
            field=field,
            requested_read=field in requested_read,
            requested_output=field in requested_output,
            requested_filter=field in requested_filter,
            required_read=field in required_read,
            required_output=field in required_output,
            required_filter=field in required_filter,
            reasons=tuple(reasons),
        ))

    has_extras = bool(extra_read or extra_output or extra_filter)
    return ExtraFieldsResult(
        status=ScopeCheckStatus.VIOLATED if has_extras else ScopeCheckStatus.PROVED,
        code="extra_fields_found" if has_extras else "no_extra_fields",
        message=(
            "Task-relative extra fields found; current output/filter dependencies "
            "must be respected before deletion."
            if has_extras else
            "No task-relative extra fields found. Sufficiency, authorization, "
            "row coverage and minimality are not proved."
        ),
        extra_read_columns=extra_read,
        extra_output_columns=extra_output,
        extra_filter_columns=extra_filter,
        field_usages=tuple(usages),
    )


@dataclass(frozen=True)
class FieldRemovalAttempt:
    """One attempted operation, including rejected and final-recheck evidence."""

    operation: Literal["remove_output", "remove_read"]
    field: str
    phase: Literal["reduction", "final_recheck"]
    accepted: bool
    code: str
    message: str
    scope_before: DataScope
    candidate_scope: Optional[DataScope] = None
    sufficiency: Optional[FieldSufficiencyResult] = None


@dataclass(frozen=True)
class FieldMinimalityResult:
    """Status describes the original request, not just the reduced candidate.

    candidate_one_step_minimal refers only to the supported removal operations.
    Residual extra filter dependencies still prevent task-relative minimality.
    No result proves row completeness, authorization or general task utility.
    """

    status: ScopeCheckStatus
    code: str
    message: str
    initial_sufficiency: FieldSufficiencyResult
    reduced_scope: Optional[DataScope] = None
    removed_read_columns: Tuple[str, ...] = ()
    removed_output_columns: Tuple[str, ...] = ()
    attempts: Tuple[FieldRemovalAttempt, ...] = ()
    final_sufficiency: Optional[FieldSufficiencyResult] = None
    final_extra_fields: Optional[ExtraFieldsResult] = None
    candidate_one_step_minimal: Optional[bool] = None
    path: Optional[str] = None


def _try_field_removal(
    scope: DataScope,
    required: DataScope,
    operation: Literal["remove_output", "remove_read"],
    field: str,
    phase: Literal["reduction", "final_recheck"],
) -> FieldRemovalAttempt:
    before = scope.model_copy(deep=True)
    if operation == "remove_read":
        dependencies = []
        if field in scope.output_columns:
            dependencies.append("output")
        if field in _filter_fields(scope.row_filter):
            dependencies.append("filter")
        if dependencies:
            return FieldRemovalAttempt(
                operation, field, phase, False, "field_dependency",
                "Read field is still used by: " + ", ".join(dependencies),
                scope_before=before,
            )

    payload = scope.model_dump(mode="python")
    attribute = "output_columns" if operation == "remove_output" else "read_columns"
    payload[attribute] = [name for name in payload[attribute] if name != field]
    try:
        candidate = DataScope.model_validate(payload)
    except ValidationError as exc:
        return FieldRemovalAttempt(
            operation, field, phase, False, "invalid_candidate",
            f"Removal would create an invalid DataScope: {exc}",
            scope_before=before,
        )
    sufficiency = check_field_sufficiency(candidate, required)
    accepted = sufficiency.status is ScopeCheckStatus.PROVED
    return FieldRemovalAttempt(
        operation, field, phase, accepted,
        "removal_preserves_sufficiency" if accepted else sufficiency.code,
        sufficiency.message,
        scope_before=before,
        candidate_scope=candidate,
        sufficiency=sufficiency,
    )


def check_field_minimality(
    requested_scope: Any,
    required_scope: Any,
) -> FieldMinimalityResult:
    """Reduce fields in memory, then independently recheck every remaining field.

    Output removals precede read removals, so dropping an extra output can unlock
    its read permission. Read fields used by the fixed predicate cannot be
    removed, including predicate fields omitted from explicit read_columns.
    Empty scopes, whole-request removal, predicate/SQL rewriting, execution and
    authorization changes are outside this operation set.

    The caller supplies task requirements (e.g. from an upstream policy).
    Tests may use annotated TaskSpec requirements; this function reads no files.
    """
    initial = check_field_sufficiency(requested_scope, required_scope)
    if initial.status is not ScopeCheckStatus.PROVED:
        return FieldMinimalityResult(
            initial.status, initial.code, initial.message,
            initial_sufficiency=initial, path=initial.path,
        )

    analysis = find_extra_fields(requested_scope, required_scope)
    if analysis.status is ScopeCheckStatus.UNKNOWN:
        return FieldMinimalityResult(
            ScopeCheckStatus.UNKNOWN, analysis.code, analysis.message,
            initial_sufficiency=initial, final_extra_fields=analysis,
            path=analysis.path,
        )

    original = _parse_scope(requested_scope, "requested_scope")
    required = _parse_scope(required_scope, "required_scope")
    current = original.model_copy(deep=True)
    attempts = []
    # With fixed predicates, output-first/read-second covers every unlock:
    # removing reads never unlocks outputs, and reads do not depend on other reads.
    for operation, attribute in (
        ("remove_output", "output_columns"),
        ("remove_read", "read_columns"),
    ):
        for field in tuple(getattr(current, attribute)):
            attempt = _try_field_removal(current, required, operation, field, "reduction")
            attempts.append(attempt)
            if attempt.accepted:
                # Evidence snapshots must not alias the returned reduced scope.
                current = attempt.candidate_scope.model_copy(deep=True)

    final_sufficiency = check_field_sufficiency(current, required)
    final_checks = []
    for operation, attribute in (
        ("remove_output", "output_columns"),
        ("remove_read", "read_columns"),
    ):
        for field in tuple(getattr(current, attribute)):
            final_checks.append(
                _try_field_removal(current, required, operation, field, "final_recheck")
            )
    attempts.extend(final_checks)
    one_step_minimal = (
        final_sufficiency.status is ScopeCheckStatus.PROVED
        and all(not attempt.accepted for attempt in final_checks)
    )
    remaining = find_extra_fields(current, required)
    removed_read = tuple(sorted(set(original.read_columns) - set(current.read_columns)))
    removed_output = tuple(sorted(set(original.output_columns) - set(current.output_columns)))

    if not one_step_minimal or remaining.status is ScopeCheckStatus.UNKNOWN:
        status = ScopeCheckStatus.UNKNOWN
        code = "final_recheck_failed"
        message = "Final field sufficiency or single-step irreducibility was not verified."
    elif remaining.status is ScopeCheckStatus.VIOLATED:
        status = ScopeCheckStatus.VIOLATED
        code = "extra_filter_dependencies_remain"
        message = (
            "Extra access remains required by the unchanged request predicate. "
            "The candidate is irreducible under supported field removals only; "
            "task-relative field minimality is not proved."
        )
    elif removed_read or removed_output:
        status = ScopeCheckStatus.VIOLATED
        code = "removable_fields_found"
        message = (
            "The original request contains removable fields. The reduced candidate "
            "is field-sufficient and single-step minimal for the supplied requirements."
        )
    else:
        status = ScopeCheckStatus.PROVED
        code = "field_minimality_proved"
        message = (
            "The original request is field-sufficient with no task-relative extras "
            "and no valid single-field removal preserves sufficiency. "
            "Row completeness, authorization and general task utility are not checked."
        )
    return FieldMinimalityResult(
        status=status, code=code, message=message,
        initial_sufficiency=initial,
        reduced_scope=current,
        removed_read_columns=removed_read,
        removed_output_columns=removed_output,
        attempts=tuple(attempts),
        final_sufficiency=final_sufficiency,
        final_extra_fields=remaining,
        candidate_one_step_minimal=one_step_minimal,
    )

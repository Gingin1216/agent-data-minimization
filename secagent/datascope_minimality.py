"""Task-relative extra fields and syntactic output/filter dependencies.

This is C2 analysis, not deletion/revalidation or a minimality proof.
Required fields come from the supplied task specification, never from merely
appearing in the requested SQL. Row predicates and max_rows are not compared.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Optional, Tuple

from pydantic import ValidationError

from .datascope_check import ScopeCheckStatus
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

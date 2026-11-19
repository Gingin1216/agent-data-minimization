"""Check field coverage against an explicit task-required DataScope.

PROVED means field sufficiency relative to the supplied requirements only.
It proves neither authorization, row/result completeness nor minimality.
Row filters and max_rows are structurally validated but never compared.
Only explicit read_columns/output_columns are compared; predicate dependencies
must already be represented in the supplied required read_columns.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Optional, Tuple

from pydantic import ValidationError

from .datascope_check import ScopeCheckStatus
from .datascope_v1 import DataScope


@dataclass(frozen=True)
class FieldSufficiencyResult:
    """Missing fields are sorted and reported for both field sets at once."""

    status: ScopeCheckStatus
    code: str
    message: str
    missing_read_columns: Tuple[str, ...] = ()
    missing_output_columns: Tuple[str, ...] = ()
    path: Optional[str] = None


def _parse_scope(value: Any, name: str) -> DataScope:
    # Revalidate models too: DataScope lists are mutable and model_copy /
    # model_construct can bypass Pydantic validation.
    if isinstance(value, DataScope):
        value = value.model_dump(mode="python")
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a DataScope or mapping")
    return DataScope.model_validate(dict(value))


def check_field_sufficiency(
    requested_scope: Any,
    required_scope: Any,
) -> FieldSufficiencyResult:
    """Check task-required read and output fields independently.

    Accept DataScope objects or mappings. Invalid/unsupported scopes return
    UNKNOWN; resource mismatches and missing fields return VIOLATED.
    Extra requested fields do not fail this sufficiency check.
    Inputs are not modified; no SQL is executed or rewritten.
    """
    scopes = []
    for value, name in (
        (requested_scope, "requested_scope"),
        (required_scope, "required_scope"),
    ):
        try:
            scopes.append(_parse_scope(value, name))
        except (ValidationError, TypeError, ValueError) as exc:
            return FieldSufficiencyResult(
                status=ScopeCheckStatus.UNKNOWN,
                code="invalid_datascope",
                message=f"{name} is not a supported DataScope: {exc}",
                path=name,
            )

    requested, required = scopes
    for attribute in ("database", "table", "operation"):
        if getattr(requested, attribute) != getattr(required, attribute):
            return FieldSufficiencyResult(
                status=ScopeCheckStatus.VIOLATED,
                code=f"{attribute}_mismatch",
                message=f"Requested {attribute} does not match the task requirement.",
                path=f"requested_scope.{attribute}",
            )

    missing_read = tuple(sorted(
        set(required.read_columns) - set(requested.read_columns)
    ))
    missing_output = tuple(sorted(
        set(required.output_columns) - set(requested.output_columns)
    ))
    if missing_read or missing_output:
        details = []
        if missing_read:
            details.append(f"missing read fields: {', '.join(missing_read)}")
        if missing_output:
            details.append(f"missing output fields: {', '.join(missing_output)}")
        return FieldSufficiencyResult(
            status=ScopeCheckStatus.VIOLATED,
            code="required_fields_missing",
            message="Field insufficiency: " + "; ".join(details),
            missing_read_columns=missing_read,
            missing_output_columns=missing_output,
        )

    return FieldSufficiencyResult(
        status=ScopeCheckStatus.PROVED,
        code="field_sufficiency_proved",
        message=(
            "All explicitly required read and output fields are covered. "
            "Authorization, row completeness and minimality are not checked."
        ),
    )

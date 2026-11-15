"""Translate a DataScope v1.0 RowFilter AST into a Z3 expression.

The translator accepts either Pydantic RowFilter nodes or equivalent JSON-like
dictionaries. Field types are always explicit; values are never used to infer
their Z3 sort.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping, Optional

import z3


class RowFilterError(Exception):
    """Raised when a RowFilter cannot be translated into the supported model."""


class UnsupportedRowFilterError(RowFilterError):
    """Raised for a known but unsupported RowFilter operator."""


SUPPORTED_FIELD_TYPES = {"string", "integer", "number", "boolean", "date_iso"}


def _field_sort(field_type: str) -> z3.SortRef:
    if not isinstance(field_type, str):
        raise RowFilterError(
            f"field type must be a string, got {type(field_type).__name__}"
        )
    if field_type == "string":
        return z3.StringSort()
    if field_type == "integer":
        return z3.IntSort()
    if field_type == "number":
        return z3.RealSort()
    if field_type == "boolean":
        return z3.BoolSort()
    if field_type == "date_iso":
        return z3.IntSort()
    raise RowFilterError(
        f"Unsupported field type {field_type!r}; expected one of {sorted(SUPPORTED_FIELD_TYPES)}"
    )


def _normalize_variables(
    field_types: Mapping[str, str],
    variables: Optional[Mapping[str, z3.ExprRef]],
) -> dict[str, z3.ExprRef]:
    """Validate and normalize caller-provided Z3 variables.

    Every field declared by ``field_types`` must have a variable, and each
    variable's Z3 sort must exactly match the declared field type. Extra
    variables are rejected because they make the translation contract unclear.
    """
    if variables is None:
        return build_field_variables(field_types)

    if not isinstance(variables, Mapping):
        raise RowFilterError("variables must be a mapping or None")

    variable_map = dict(variables)
    missing = [field for field in field_types if field not in variable_map]
    if missing:
        raise RowFilterError(
            f"variables is missing fields declared in field_types: {sorted(missing)}"
        )

    extra = [field for field in variable_map if field not in field_types]
    if extra:
        raise RowFilterError(
            f"variables contains fields not declared in field_types: {sorted(extra)}"
        )

    for field, field_type in field_types.items():
        variable = variable_map[field]
        expected_sort = _field_sort(field_type)
        if not isinstance(variable, z3.ExprRef):
            raise RowFilterError(
                f"Variable for {field!r} is not a Z3 expression: {variable!r}"
            )
        if variable.sort() != expected_sort:
            raise RowFilterError(
                f"Variable for {field!r} has sort {variable.sort()}, "
                f"expected {expected_sort} for type {field_type!r}"
            )

    return variable_map


def _field_variable(field: str, field_type: str) -> z3.ExprRef:
    if field_type == "string":
        return z3.String(f"field_{field}")
    if field_type == "integer":
        return z3.Int(f"field_{field}")
    if field_type == "number":
        return z3.Real(f"field_{field}")
    if field_type == "boolean":
        return z3.Bool(f"field_{field}")
    if field_type == "date_iso":
        return z3.Int(f"field_{field}")
    raise RowFilterError(
        f"Unsupported field type {field_type!r}; expected one of {sorted(SUPPORTED_FIELD_TYPES)}"
    )


def build_field_variables(field_types: Mapping[str, str]) -> dict[str, z3.ExprRef]:
    """Create one Z3 variable for every entry in the explicit field type table."""
    if not isinstance(field_types, Mapping):
        raise RowFilterError("field_types must be a mapping")

    variables: dict[str, z3.ExprRef] = {}
    for field, field_type in field_types.items():
        if not isinstance(field, str) or not field:
            raise RowFilterError("field names must be non-empty strings")
        if not isinstance(field_type, str):
            raise RowFilterError(
                f"field type for {field!r} must be a string, "
                f"got {type(field_type).__name__}"
            )
        if field_type not in SUPPORTED_FIELD_TYPES:
            raise RowFilterError(
                f"Unsupported field type {field_type!r} for {field!r}; "
                f"expected one of {sorted(SUPPORTED_FIELD_TYPES)}"
            )
        variables[field] = _field_variable(field, field_type)
    return variables


def _get(node: Any, key: str) -> Any:
    if isinstance(node, dict):
        if key not in node:
            raise RowFilterError(f"RowFilter node is missing {key!r}")
        return node[key]
    try:
        return getattr(node, key)
    except AttributeError as exc:
        raise RowFilterError(f"RowFilter node is missing {key!r}") from exc


def _literal(value: Any, field_type: str) -> z3.ExprRef:
    if field_type == "string":
        if not isinstance(value, str):
            raise RowFilterError(
                f"Expected string value for field type 'string', got {value!r}"
            )
        return z3.StringVal(value)
    if field_type == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise RowFilterError(
                f"Expected integer value for field type 'integer', got {value!r}"
            )
        return z3.IntVal(value)
    if field_type == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise RowFilterError(
                f"Expected numeric value for field type 'number', got {value!r}"
            )
        return z3.RealVal(value)
    if field_type == "boolean":
        if not isinstance(value, bool):
            raise RowFilterError(
                f"Expected boolean value for field type 'boolean', got {value!r}"
            )
        return z3.BoolVal(value)
    if field_type == "date_iso":
        if not isinstance(value, str):
            raise RowFilterError(
                f"Expected YYYY-MM-DD string for field type 'date_iso', "
                f"got {value!r}"
            )
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise RowFilterError(f"Invalid ISO date {value!r}") from exc
        if parsed.isoformat() != value:
            raise RowFilterError(f"Invalid ISO date format {value!r}")
        return z3.IntVal(int(value.replace("-", "")))
    raise RowFilterError(f"Unsupported field type {field_type!r}")


def _variable_for(
    field: str,
    field_types: Mapping[str, str],
    variables: dict[str, z3.ExprRef],
) -> z3.ExprRef:
    if not isinstance(field, str) or field not in field_types:
        raise RowFilterError(f"Field {field!r} is not present in field_types")
    if field not in variables:
        raise RowFilterError(f"Variable for {field!r} is missing")
    return variables[field]


def _convert(
    node: Any,
    field_types: Mapping[str, str],
    variables: dict[str, z3.ExprRef],
) -> z3.BoolRef:
    if node is None:
        raise RowFilterError("RowFilter node is None")

    op = _get(node, "op")

    if op == "true":
        return z3.BoolVal(True)

    if op == "eq":
        field = _get(node, "field")
        value = _get(node, "value")
        if not isinstance(field, str) or field not in field_types:
            raise RowFilterError(f"Field {field!r} is not present in field_types")
        field_type = field_types[field]
        return _variable_for(field, field_types, variables) == _literal(value, field_type)

    if op == "in":
        field = _get(node, "field")
        values = _get(node, "values")
        if not isinstance(field, str) or field not in field_types:
            raise RowFilterError(f"Field {field!r} is not present in field_types")
        field_type = field_types[field]
        if not isinstance(values, list) or not values:
            raise RowFilterError("in RowFilter requires a non-empty values list")
        variable = _variable_for(field, field_types, variables)
        return z3.Or([variable == _literal(value, field_type) for value in values])

    if op in {"lt", "lte", "gt", "gte"}:
        field = _get(node, "field")
        value = _get(node, "value")
        if not isinstance(field, str) or field not in field_types:
            raise RowFilterError(f"Field {field!r} is not present in field_types")
        field_type = field_types[field]
        if field_type == "boolean":
            raise UnsupportedRowFilterError(
                f"Comparison {op!r} is not defined for boolean field {field!r}"
            )
        if field_type == "string":
            raise UnsupportedRowFilterError(
                f"Comparison {op!r} is not supported for string field {field!r}: "
                "database collation semantics are not guaranteed"
            )
        variable = _variable_for(field, field_types, variables)
        literal = _literal(value, field_type)
        if op == "lt":
            return variable < literal
        if op == "lte":
            return variable <= literal
        if op == "gt":
            return variable > literal
        return variable >= literal

    if op == "and":
        conditions = _get(node, "conditions")
        if not isinstance(conditions, list) or len(conditions) < 2:
            raise RowFilterError("and RowFilter requires at least two conditions")
        return z3.And([_convert(condition, field_types, variables) for condition in conditions])

    raise UnsupportedRowFilterError(f"Unsupported RowFilter operator: {op!r}")


def row_filter_to_z3(
    node: Any,
    field_types: Mapping[str, str],
    variables: Optional[Mapping[str, z3.ExprRef]] = None,
) -> z3.BoolRef:
    """Translate a DataScope RowFilter node into a Z3 expression.

    The optional ``variables`` mapping lets callers reuse the same field symbols
    across multiple filters, which is required for later implication checks.
    """
    try:
        variable_map = _normalize_variables(field_types, variables)
        return _convert(node, field_types, variable_map)
    except RowFilterError:
        raise
    except Exception as exc:
        raise RowFilterError(f"Failed to translate RowFilter: {exc}") from exc

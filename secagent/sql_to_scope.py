"""Convert a deliberately small SQL subset into a DataScope RequestedScope.

Supported syntax is intentionally narrow:

* SELECT
* one table
* explicit column list
* WHERE built from ``=``, ``IN``, ``>``, ``>=``, ``<``, ``<=`` and ``AND``
* LIMIT with a positive integer

Unsupported SQL is rejected explicitly. This module never guesses a LIMIT,
table, column, or filter condition.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from typing import Any, Optional

import sqlglot
from sqlglot import exp

from .datascope_v1 import DataScope


class SQLScopeError(ValueError):
    """Raised internally when SQL is outside the supported conversion subset."""


@dataclass(frozen=True)
class SQLToScopeResult:
    ok: bool
    scope: Optional[DataScope] = None
    reason: Optional[str] = None


def _reject(message: str) -> None:
    raise SQLScopeError(message)


def _filter_placeholders(node: exp.Expression) -> list[exp.Placeholder]:
    """Return WHERE placeholders in SQL source order for the supported AST."""
    if isinstance(node, exp.Paren):
        return _filter_placeholders(node.this)
    if isinstance(node, exp.And):
        return _filter_placeholders(node.this) + _filter_placeholders(node.expression)
    if isinstance(node, exp.In):
        placeholders: list[exp.Placeholder] = []
        for value in node.expressions:
            placeholders.extend(value.find_all(exp.Placeholder))
        return placeholders
    if isinstance(node, (exp.EQ, exp.GT, exp.GTE, exp.LT, exp.LTE)):
        return list(node.expression.find_all(exp.Placeholder))
    return list(node.find_all(exp.Placeholder))


def _bind_parameters(tree: exp.Expression, params: Any) -> exp.Expression:
    placeholders = list(tree.find_all(exp.Placeholder))
    if not placeholders:
        if params not in (None, (), [], {}):
            _reject("parameters were provided but SQL contains no placeholders")
        return tree

    if isinstance(params, Mapping):
        values = params
        for placeholder in placeholders:
            name = placeholder.this
            if not isinstance(name, str) or name not in values:
                _reject(f"missing named SQL parameter: {name!r}")
            placeholder.replace(exp.convert(values[name]))
        return tree

    if not isinstance(params, Sequence) or isinstance(params, (str, bytes)):
        _reject("positional SQL parameters must be a list or tuple")

    # sqlglot does not expose source offsets for bare placeholders, and
    # ``find_all`` may visit LIMIT before WHERE. The supported SQL subset has a
    # fixed source order: WHERE placeholders come before the LIMIT placeholder.
    ordered_placeholders: list[exp.Placeholder] = []
    where = tree.args.get("where")
    if where is not None:
        ordered_placeholders.extend(_filter_placeholders(where.this))
    limit = tree.args.get("limit")
    if limit is not None:
        ordered_placeholders.extend(limit.find_all(exp.Placeholder))

    ordered_ids = {id(placeholder) for placeholder in ordered_placeholders}
    unexpected = [
        placeholder for placeholder in placeholders if id(placeholder) not in ordered_ids
    ]
    if unexpected:
        _reject("placeholders are only supported in WHERE and LIMIT")

    if len(params) != len(ordered_placeholders):
        _reject(
            f"expected {len(ordered_placeholders)} positional parameters, got {len(params)}"
        )
    for placeholder, value in zip(ordered_placeholders, params):
        placeholder.replace(exp.convert(value))
    return tree


def _literal_value(node: exp.Expression) -> Any:
    negative = isinstance(node, exp.Neg)
    literal = node.this if negative else node
    if isinstance(literal, exp.Literal) and (not negative or literal.is_number):
        value = literal.to_py()
        if literal.is_number:
            if not isinstance(value, int):
                value = float(value)
            if negative:
                value = -value
            if isinstance(value, int) and not -(2**63) <= value < 2**63:
                _reject("integer literal is outside SQLite's signed 64-bit range")
            if isinstance(value, float) and not isfinite(value):
                _reject("SQL numeric literal must be finite")
        return value
    if isinstance(node, exp.Boolean):
        return node.this
    if isinstance(node, exp.Null):
        _reject("NULL is not supported in DataScope row filters")
    _reject(f"unsupported SQL literal: {node.sql(dialect='sqlite')}")


def _column_name(node: exp.Expression, table_name: str, table_alias: str) -> str:
    if not isinstance(node, exp.Column):
        _reject(f"expected a column, got {node.sql(dialect='sqlite')}")
    if node.table and node.table not in {table_name, table_alias}:
        _reject(f"column {node.sql(dialect='sqlite')} references another table")
    if not node.name:
        _reject("empty SQL column name")
    return node.name


def _filter_node(
    node: exp.Expression,
    table_name: str,
    table_alias: str,
) -> dict[str, Any]:
    if isinstance(node, exp.Paren):
        return _filter_node(node.this, table_name, table_alias)

    if isinstance(node, exp.And):
        return {
            "op": "and",
            "conditions": [
                _filter_node(node.this, table_name, table_alias),
                _filter_node(node.expression, table_name, table_alias),
            ],
        }

    if isinstance(node, exp.In):
        field = _column_name(node.this, table_name, table_alias)
        values = [_literal_value(value) for value in node.expressions]
        if not values:
            _reject("IN condition must contain at least one value")
        return {"op": "in", "field": field, "values": values}

    comparison_ops = {
        exp.EQ: "eq",
        exp.GT: "gt",
        exp.GTE: "gte",
        exp.LT: "lt",
        exp.LTE: "lte",
    }
    for expression_type, op in comparison_ops.items():
        if isinstance(node, expression_type):
            field = _column_name(node.this, table_name, table_alias)
            return {
                "op": op,
                "field": field,
                "value": _literal_value(node.expression),
            }

    _reject(f"unsupported WHERE expression: {node.sql(dialect='sqlite')}")


def _extract_limit(tree: exp.Select) -> int:
    limit = tree.args.get("limit")
    if limit is None:
        _reject("SQL has no LIMIT; max_rows cannot be inferred")
    if not isinstance(limit, exp.Limit) or limit.args.get("limit_options") is not None:
        _reject("only a simple LIMIT is supported")
    value = _literal_value(limit.expression)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        _reject("LIMIT must be a positive integer")
    return value


def _parse_tree(sql: str, params: Any) -> exp.Select:
    if not isinstance(sql, str) or not sql.strip():
        _reject("SQL must be a non-empty string")
    statements = sqlglot.parse(sql, read="sqlite")
    statements = [statement for statement in statements if statement is not None]
    if len(statements) != 1:
        _reject("exactly one SQL statement is required")
    tree = statements[0]
    if not isinstance(tree, exp.Select):
        _reject("only SELECT statements are supported")
    if tree.args.get("with_"):
        _reject("CTEs are not supported")
    if tree.args.get("joins"):
        _reject("JOIN is not supported")
    if tree.args.get("group"):
        _reject("GROUP BY is not supported")
    if tree.args.get("having"):
        _reject("HAVING is not supported")
    if tree.args.get("order"):
        _reject("ORDER BY is not supported")
    if tree.args.get("distinct") is not None:
        _reject("SELECT DISTINCT is not supported")
    if tree.args.get("offset") is not None:
        _reject("OFFSET is not supported")
    if list(tree.find_all(exp.Subquery)):
        _reject("subqueries are not supported")
    if list(tree.find_all(exp.AggFunc)):
        _reject("aggregate functions are not supported")
    return _bind_parameters(tree, params)


def sql_to_scope(
    sql: str,
    params: Any,
    schema: Mapping[str, Mapping[str, str]],
    database: str = "company",
) -> SQLToScopeResult:
    """Convert supported SQL into a RequestedScope.

    ``params`` must be a positional sequence for ``?`` placeholders, a mapping
    for named ``:name`` placeholders, or ``None``. Values are bound into the
    AST, never interpolated into the SQL string.

    Columns referenced by WHERE are included in ``read_columns``. Trusted
    execution-only filter fields are not supported by this conservative first
    version and must be introduced through a separately reviewed interface.
    """
    try:
        tree = _parse_tree(sql, params)
        tables = list(tree.find_all(exp.Table))
        if len(tables) != 1:
            _reject(f"expected exactly one table, found {len(tables)}")
        table_node = tables[0]
        table_name = table_node.name
        table_alias = table_node.alias or table_name
        if table_node.db and table_node.db != database:
            _reject(f"unknown database qualifier: {table_node.db!r}")
        if table_name not in schema:
            _reject(f"unknown table: {table_name!r}")
        table_schema = schema[table_name]

        selects = tree.expressions
        if not selects:
            _reject("SELECT must contain an explicit column list")
        output_columns: list[str] = []
        for select in selects:
            if not isinstance(select, exp.Column):
                _reject(
                    f"only explicit column selections are supported, got "
                    f"{select.sql(dialect='sqlite')}"
                )
            output_columns.append(_column_name(select, table_name, table_alias))

        where = tree.args.get("where")
        filter_fields: list[str] = []
        if where is None:
            row_filter = {"op": "true"}
        else:
            row_filter = _filter_node(where.this, table_name, table_alias)
            filter_fields = [
                _column_name(column, table_name, table_alias)
                for column in where.find_all(exp.Column)
            ]

        read_columns = sorted(set(output_columns) | set(filter_fields))
        for column in read_columns:
            if column not in table_schema:
                _reject(f"unknown column: {table_name}.{column}")

        max_rows = _extract_limit(tree)
        scope = DataScope.model_validate(
            {
                "schema_version": "1.0",
                "database": database,
                "table": table_name,
                "operation": "SELECT",
                "read_columns": read_columns,
                "output_columns": output_columns,
                "row_filter": row_filter,
                "max_rows": max_rows,
            }
        )
        return SQLToScopeResult(ok=True, scope=scope)
    except SQLScopeError as exc:
        return SQLToScopeResult(ok=False, reason=str(exc))
    except Exception as exc:
        return SQLToScopeResult(ok=False, reason=f"SQL conversion failed: {exc}")

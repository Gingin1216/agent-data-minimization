"""DataScope v1.0 -- Progent 数据语义级授权的独立数据模型。

只定义数据结构与结构校验，不负责 SQL 解析、授权判定、SMT 或 SQLite 执行。
依赖：pydantic>=2（原版 Progent 已依赖）。支持 Python 3.9+。
"""

from __future__ import annotations

import json
import re
from typing import Annotated, List, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr, field_validator, model_validator


# v1 只接受简单 SQL 标识符；带引号、带点号或通配符的名称不在本版范围内。
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
Scalar = Union[StrictStr, StrictInt, StrictFloat, StrictBool]


def _check_identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"非法标识符: {value!r}（v1 仅支持简单名称）")
    return value


class _Node(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class TrueFilter(_Node):
    op: Literal["true"] = "true"


class EqFilter(_Node):
    op: Literal["eq"] = "eq"
    field: str
    value: Scalar

    @field_validator("field")
    @classmethod
    def check_field(cls, value: str) -> str:
        return _check_identifier(value)


class InFilter(_Node):
    op: Literal["in"] = "in"
    field: str
    values: List[Scalar] = Field(min_length=1)

    @field_validator("field")
    @classmethod
    def check_field(cls, value: str) -> str:
        return _check_identifier(value)

    @field_validator("values")
    @classmethod
    def check_values(cls, values: List[Scalar]) -> List[Scalar]:
        # 不混用不同值类型，以免引入隐式类型转换歧义。
        if any(type(v) is not type(values[0]) for v in values[1:]):
            raise ValueError("IN 的 values 必须使用同一种基本类型")
        return values


class CompareFilter(_Node):
    op: Literal["lt", "lte", "gt", "gte"]
    field: str
    value: Union[StrictStr, StrictInt, StrictFloat]

    @field_validator("field")
    @classmethod
    def check_field(cls, value: str) -> str:
        return _check_identifier(value)


class AndFilter(_Node):
    op: Literal["and"] = "and"
    conditions: List["RowFilter"] = Field(min_length=2)


# RowFilter 采用带 op 标签的判别联合，便于第 4 组扩展逻辑证明。
RowFilter = Annotated[
    Union[TrueFilter, EqFilter, InFilter, CompareFilter, AndFilter],
    Field(discriminator="op"),
]
AndFilter.model_rebuild()


class DataScope(BaseModel):
    """一个对象 = 一个逻辑数据库 + 一张表 + 一种操作（v1 仅 SELECT）。

    对象本身不包含 user_id、任务、策略版本或工具名；由上层 PolicyRecord 关联。
    AuthorizedScope 和 RequestedScope 都使用该结构，角色由调用者确定。
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["1.0"]
    database: str
    table: str
    operation: Literal["SELECT"]
    read_columns: List[str] = Field(min_length=1)
    output_columns: List[str] = Field(min_length=1)
    row_filter: RowFilter
    max_rows: int = Field(gt=0, strict=True)

    @field_validator("database", "table")
    @classmethod
    def check_resource(cls, value: str) -> str:
        return _check_identifier(value)

    @field_validator("read_columns", "output_columns")
    @classmethod
    def check_columns(cls, values: List[str]) -> List[str]:
        for value in values:
            _check_identifier(value)
        if len(values) != len(set(values)):
            raise ValueError("字段集合中不允许出现重复列")
        return sorted(values)  # 规范化字段顺序，便于比较、审计和序列化。

    @model_validator(mode="after")
    def check_column_inclusion(self) -> "DataScope":
        if not set(self.output_columns).issubset(self.read_columns):
            raise ValueError("output_columns 必须是 read_columns 的子集")
        return self

    def as_json(self) -> str:
        """稳定地序列化为 JSON，方便跨组传输。"""
        return json.dumps(self.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, indent=2)


if __name__ == "__main__":
    # 示范授权：只能查询销售部门的员工姓名，最多返回 10 行。
    scope = DataScope(
        schema_version="1.0",
        database="company",
        table="employees",
        operation="SELECT",
        read_columns=["name", "department"],
        output_columns=["name"],
        row_filter=EqFilter(field="department", value="sales"),
        max_rows=10,
    )
    print(scope.as_json())
    restored = DataScope.model_validate_json(scope.as_json())
    assert restored == scope

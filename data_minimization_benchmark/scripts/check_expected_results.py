"""核对 reference_sql 的执行结果与 expected_result 是否一致。

用法（任意目录均可）：
    python data_minimization_benchmark/scripts/check_expected_results.py

规则：
* type=table：row_count / columns / sum_amount / rows / boundary_row 逐项比对；
* type=record：单行结果按列名逐字段比对；
* type=scalar：结果集中的数值与 value 里的数值标量逐一比对（容差 0.01）；
* 无 reference_sql 的任务跳过（拒绝类任务无数值可验，不算缺口）。

退出码：0 全部一致，1 存在不一致。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve()
BENCH = SCRIPT.parents[1]
DB = BENCH / "environment" / "database" / "company.db"
SPEC_DIR = BENCH / "dataset" / "task_specs"

TOL = 0.01


def close(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= TOL
    return a == b


def row_matches(want, got) -> bool:
    return len(want) == len(got) and all(close(a, b) for a, b in zip(want, got))


def check_table(tid: str, value: dict, columns: list[str], rows: list[tuple], errors: list[str]) -> None:
    if "row_count" in value and len(rows) != value["row_count"]:
        errors.append(f"{tid} row_count：spec={value['row_count']} 实际={len(rows)}")
    if "columns" in value and list(columns) != list(value["columns"]):
        errors.append(f"{tid} columns：spec={value['columns']} 实际={list(columns)}")
    if "sum_amount" in value:
        if "amount" in columns:
            idx = columns.index("amount")
            actual = round(sum(float(r[idx]) for r in rows), 2)
            if not close(actual, value["sum_amount"]):
                errors.append(f"{tid} sum_amount：spec={value['sum_amount']} 实际={actual}")
        else:
            errors.append(f"{tid} sum_amount 无法机判：结果集没有 amount 列")
    if "rows" in value and len(value["rows"]) == len(rows):
        for i, (w, g) in enumerate(zip(value["rows"], rows)):
            if not row_matches(w, g):
                errors.append(f"{tid} rows[{i}]：spec={w} 实际={list(g)}")
    if "boundary_row" in value:
        if not any(row_matches(value["boundary_row"], g) for g in rows):
            errors.append(f"{tid} boundary_row {value['boundary_row']} 不在结果集中")


def check_record(tid: str, value: dict, columns: list[str], rows: list[tuple], errors: list[str]) -> None:
    if len(rows) != 1:
        errors.append(f"{tid} record 期望 1 行，实际 {len(rows)} 行")
        return
    for k, v in value.items():
        if k not in columns:
            errors.append(f"{tid} record 字段 {k} 不在结果列 {list(columns)} 中")
        elif not close(rows[0][columns.index(k)], v):
            errors.append(f"{tid} record {k}：spec={v!r} 实际={rows[0][columns.index(k)]!r}")


def check_scalar(tid: str, value: dict, rows: list[tuple], errors: list[str]) -> None:
    nums = [float(v) for r in rows for v in r if isinstance(v, (int, float))]
    for k, v in value.items():
        if isinstance(v, (int, float)) and not any(abs(v - n) <= TOL for n in nums):
            errors.append(f"{tid} scalar {k}={v} 不在 SQL 结果数值 {nums} 中")


def main() -> int:
    if not DB.exists():
        print("SKIP：company.db 不存在，先跑 environment/database/init_db.py")
        return 0

    con = sqlite3.connect(DB)
    cur = con.cursor()
    checked = 0
    errors: list[str] = []

    for path in sorted(SPEC_DIR.glob("*.json")):
        spec = json.loads(path.read_text(encoding="utf-8"))
        sql = spec.get("reference_sql")
        if not sql:
            continue
        tid = spec["task_id"]
        expected = spec.get("expected_result") or {}
        etype = expected.get("type")
        value = expected.get("value") or {}
        checked += 1

        try:
            cur.execute(sql)
            rows = cur.fetchall()
            columns = [d[0] for d in cur.description]
        except sqlite3.Error as e:
            errors.append(f"{tid} reference_sql 执行失败：{e}")
            continue

        before = len(errors)
        if etype == "table":
            check_table(tid, value, columns, rows, errors)
        elif etype == "record":
            check_record(tid, value, columns, rows, errors)
        elif etype == "scalar":
            check_scalar(tid, value, rows, errors)
        else:
            errors.append(f"{tid} 未知 expected_result.type：{etype!r}")

        if len(errors) == before:
            print(f"OK    {tid} type={etype} rows={len(rows)}")

    con.close()
    print(f"reference_sql_checked={checked}  mismatches={len(errors)}")
    for e in errors:
        print("ERROR", e)
    print("结果：通过" if not errors else "结果：不通过")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

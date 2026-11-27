"""核对每条 TaskSpec 的 requested_query 与实现层期望动作是否一致。

用法（任意目录均可）：
    python data_minimization_benchmark/scripts/check_requested_scope.py

规则：
* implementation_action=DENY 的任务，requested_query 必须被 sql_to_scope 拒绝；
* implementation_action=DENY 且 deny_by=policy 的任务，requested_query 必须能转换，
  由策略层按 deny_reason 拒绝（例如 DB-013 的跨部门边界）；
* implementation_action=ALLOW 的任务，requested_query 必须成功转换，
  且转换结果与 must_visit.datascope[0] 一致。

退出码：0 全部一致，1 存在不一致。
"""
from __future__ import annotations

import contextlib
import io
import json
import re
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve()
BENCH = SCRIPT.parents[1]
REPO = SCRIPT.parents[2]
sys.path.insert(0, str(REPO))
with contextlib.redirect_stderr(io.StringIO()):  # 压住 secagent 导入时的配置打印
    from secagent.sql_to_scope import sql_to_scope

SPEC_DIR = BENCH / "dataset" / "task_specs"
SCHEMA_SQL = BENCH / "environment" / "database" / "schema.sql"


def parse_schema(sql_text: str) -> dict:
    """从 schema.sql 提取 表→列集合，结构与 sql_to_scope 需要的 schema 入参同构。"""
    schema = {}
    for m in re.finditer(r"CREATE TABLE\s+(\w+)\s*\((.*?)\);", sql_text, re.S):
        cols = []
        for line in m.group(2).splitlines():
            line = line.strip().rstrip(",")
            cm = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s", line)
            if cm and cm.group(1).upper() not in ("PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT"):
                cols.append(cm.group(1))
        schema[m.group(1)] = {c: "TEXT" for c in cols}
    return schema


def main() -> int:
    schema = parse_schema(SCHEMA_SQL.read_text(encoding="utf-8"))
    checked = 0
    rejected = 0
    errors = []

    for path in sorted(SPEC_DIR.glob("*.json")):
        spec = json.loads(path.read_text(encoding="utf-8"))
        query = spec.get("requested_query")
        if not query:
            continue
        checked += 1
        result = sql_to_scope(query, None, schema)
        impl = spec.get("implementation_action", spec.get("expected_action"))

        if impl == "DENY":
            if spec.get("deny_by") == "policy":
                if not result.ok:
                    errors.append(
                        f"{spec['task_id']} 期望策略拒绝，但 SQL 语法转换失败：{result.reason}"
                    )
                else:
                    rejected += 1
                    print(
                        f"POLICY {spec['task_id']} 语法通过，按策略拒绝："
                        f"{spec.get('deny_reason', 'policy')}"
                    )
                continue
            if result.ok:
                errors.append(f"{spec['task_id']} 期望拒绝，但 SQL 转换成功了")
            else:
                rejected += 1
                print(f"DENY  {spec['task_id']} 拒绝原因：{result.reason}")
            continue

        if not result.ok:
            errors.append(f"{spec['task_id']} 期望放行，但转换被拒：{result.reason}")
            continue
        want = spec["must_visit"]["datascope"][0]
        got = result.scope.model_dump(mode="json")
        for key in ("table", "read_columns", "output_columns", "row_filter", "max_rows"):
            if got.get(key) != want.get(key):
                errors.append(
                    f"{spec['task_id']} 转换结果与 must_visit 不一致：{key} "
                    f"{got.get(key)!r} != {want.get(key)!r}"
                )

    print(f"requested_queries_checked={checked}  rejected_as_expected={rejected}  mismatches={len(errors)}")
    for e in errors:
        print("ERROR", e)
    print("结果：通过" if not errors else "结果：不通过")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

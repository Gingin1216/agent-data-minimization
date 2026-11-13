"""校验 TaskSpec 与 TaskCatalog 的结构一致性和 DataScope v1.0 兼容性。

用法（本目录下）：
    python scripts/validate_tasks.py
    python scripts/validate_tasks.py --check-env-hash   # 额外核对 env_hash 与当前环境一致

退出码：0 = 全部通过（允许存在 warning），1 = 存在 error。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SPEC_DIR = BASE / "dataset" / "task_specs"
CATALOG = BASE / "dataset" / "task_catalog.csv"
ENV_DIR = BASE / "environment"

IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ACTIONS = {"ALLOW", "CONSTRAIN", "DENY", "ASK"}
CATEGORIES = {"normal", "over", "unauthorized", "injection", "boundary"}
DIFFICULTIES = {"L1", "L2", "L3"}
SCENARIOS = {"database", "filesystem"}

REQUIRED_TOP = [
    "task_id", "type", "title", "scenario", "category", "difficulty", "user_query",
    "user_identity", "environment", "must_visit", "forbidden_scope",
    "expected_action", "expected_result", "utility_check", "security_check",
    "minimality", "decision_points", "label",
]
REQUIRED_ENV = ["environment_id", "env_hash", "seed", "db_version", "fs_version"]
REQUIRED_LABEL = ["annotator", "reviewer", "confidence"]

ROWFILTER_KEYS = {
    "true": set(),
    "eq": {"field", "value"},
    "in": {"field", "values"},
    "lt": {"field", "value"}, "lte": {"field", "value"},
    "gt": {"field", "value"}, "gte": {"field", "value"},
    "and": {"conditions"},
}
COMPARE_OPS = {"lt", "lte", "gt", "gte"}
SUPPORTED_OPS = set(ROWFILTER_KEYS)

errors: list[str] = []
warnings: list[str] = []


def err(tid: str, msg: str) -> None:
    errors.append(f"[{tid}] {msg}")


def warn(tid: str, msg: str) -> None:
    warnings.append(f"[{tid}] {msg}")


def is_pos_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v > 0


def check_ident(tid: str, label: str, value) -> None:
    if not isinstance(value, str) or not IDENT.match(value):
        err(tid, f"{label} 不是合法标识符：{value!r}（只允许 ^[A-Za-z_][A-Za-z0-9_]*$）")


def check_row_filter(tid: str, where: str, node) -> None:
    if not isinstance(node, dict):
        err(tid, f"{where} row_filter 必须是对象，实际 {type(node).__name__}")
        return
    op = node.get("op")
    if op not in SUPPORTED_OPS:
        err(tid, f"{where} row_filter 使用了不支持的 op：{op!r}")
        return
    allowed = ROWFILTER_KEYS[op] | {"op"}
    extra = set(node) - allowed
    if extra:
        err(tid, f"{where} row_filter({op}) 含未知字段：{sorted(extra)}")
    missing = ROWFILTER_KEYS[op] - set(node)
    if missing:
        err(tid, f"{where} row_filter({op}) 缺少字段：{sorted(missing)}")
    if op in {"eq", "in"} or op in COMPARE_OPS:
        check_ident(tid, f"{where} row_filter.field", node.get("field"))
    if op == "in":
        vals = node.get("values")
        if not isinstance(vals, list) or not vals:
            err(tid, f"{where} in 的 values 必须是非空列表")
        elif len({type(v).__name__ for v in vals}) > 1:
            err(tid, f"{where} in 的 values 类型不一致")
    if op == "and":
        conds = node.get("conditions")
        if not isinstance(conds, list) or len(conds) < 2:
            err(tid, f"{where} and 至少需要 2 个子条件")
        else:
            for i, c in enumerate(conds):
                check_row_filter(tid, f"{where}.and[{i}]", c)


def check_datascope(tid: str, where: str, ds) -> None:
    if not isinstance(ds, dict):
        err(tid, f"{where} 的 DataScope 必须是对象")
        return
    expect = {"schema_version", "database", "table", "operation",
              "read_columns", "output_columns", "row_filter", "max_rows"}
    extra = set(ds) - expect
    if extra:
        err(tid, f"{where} DataScope 含未知字段（extra=forbid）：{sorted(extra)}")
    missing = expect - set(ds)
    if missing:
        err(tid, f"{where} DataScope 缺字段：{sorted(missing)}")
        return
    if ds["schema_version"] != "1.0":
        err(tid, f"{where} schema_version 必须为 '1.0'")
    check_ident(tid, f"{where} database", ds["database"])
    check_ident(tid, f"{where} table", ds["table"])
    if ds["operation"] != "SELECT":
        err(tid, f"{where} operation 目前只支持 SELECT，实际 {ds['operation']!r}")

    rc, oc = ds["read_columns"], ds["output_columns"]
    for name, cols in (("read_columns", rc), ("output_columns", oc)):
        if not isinstance(cols, list) or not cols:
            err(tid, f"{where} {name} 必须是非空列表")
            continue
        if len(set(cols)) != len(cols):
            err(tid, f"{where} {name} 有重复列")
        for c in cols:
            check_ident(tid, f"{where} {name}", c)
    if isinstance(rc, list) and isinstance(oc, list):
        extra_cols = set(oc) - set(rc)
        if extra_cols:
            err(tid, f"{where} output_columns 必须是 read_columns 子集，多出 {sorted(extra_cols)}")

    if not is_pos_int(ds["max_rows"]):
        err(tid, f"{where} max_rows 必须是严格正整数，实际 {ds['max_rows']!r}")
    check_row_filter(tid, where, ds["row_filter"])


def check_scope_block(tid: str, where: str, block) -> None:
    if not isinstance(block, dict):
        err(tid, f"{where} 必须是对象")
        return
    for key in ("datascope", "paths"):
        for i, ds in enumerate(block.get(key, []) or []):
            if key == "datascope":
                check_datascope(tid, f"{where}.{key}[{i}]", ds)
            else:
                p = ds.get("path") if isinstance(ds, dict) else None
                if not isinstance(p, str) or not p:
                    err(tid, f"{where}.paths[{i}] 缺少 path")
                ops = ds.get("operations") if isinstance(ds, dict) else None
                if not isinstance(ops, list) or not ops:
                    err(tid, f"{where}.paths[{i}] 缺少 operations")


def table_column_sets(block: dict) -> dict[tuple[str, str], set[str]]:
    out: dict[tuple[str, str], set[str]] = {}
    for ds in block.get("datascope", []) or []:
        if not isinstance(ds, dict):
            continue
        key = (ds.get("database"), ds.get("table"))
        out.setdefault(key, set()).update(ds.get("read_columns") or [])
    return out


def env_hash_now() -> str:
    h = hashlib.sha256()
    schema = ENV_DIR / "database" / "schema.sql"
    init = ENV_DIR / "database" / "init_db.py"
    h.update(schema.read_bytes())
    h.update(init.read_bytes())
    h.update(b"20261111")
    return "sha256:" + h.hexdigest()[:16]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-env-hash", action="store_true")
    args = parser.parse_args()

    # 1. catalog
    if not CATALOG.exists():
        print(f"缺少 catalog：{CATALOG}")
        return 1
    rows = list(csv.DictReader(CATALOG.read_text(encoding="utf-8").splitlines()))
    ids = [r["task_id"] for r in rows]
    if len(ids) != len(set(ids)):
        errors.append("catalog 中 task_id 有重复")
    for r in rows:
        tid = r["task_id"]
        if r["scenario"] not in SCENARIOS:
            err(tid, f"scenario 非法：{r['scenario']}")
        if r["category"] not in CATEGORIES:
            err(tid, f"category 非法：{r['category']}")
        if r["difficulty"] not in DIFFICULTIES:
            err(tid, f"difficulty 非法：{r['difficulty']}")
        if r["expected_action"] not in ACTIONS:
            err(tid, f"expected_action 非法：{r['expected_action']}")
        if r["injection"] not in {"yes", "no"}:
            err(tid, f"injection 必须写 yes/no，实际 {r['injection']!r}")
        if not r["decision_points"].isdigit() or int(r["decision_points"]) < 1:
            err(tid, "decision_points 必须是 >=1 的整数")
        if r["spec_status"] == "pilot" and not (SPEC_DIR / f"{tid}.json").exists():
            err(tid, "catalog 标记为 pilot 但缺少 spec 文件")
        if r["injection"] == "yes" and r["category"] != "injection":
            warn(tid, "含注入但 category 不是 injection")

    # 2. specs
    specs = sorted(SPEC_DIR.glob("*.json"))
    if not specs:
        errors.append("task_specs 目录为空")
    catalog_by_id = {r["task_id"]: r for r in rows}
    expected_env = env_hash_now()

    for path in specs:
        tid = path.stem
        try:
            spec = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            err(tid, f"JSON 解析失败：{e}")
            continue

        missing = [k for k in REQUIRED_TOP if k not in spec]
        if missing:
            err(tid, f"缺少顶层字段：{missing}")

        if spec.get("scenario") not in SCENARIOS:
            err(tid, f"scenario 非法：{spec.get('scenario')}")
        if spec.get("category") not in CATEGORIES:
            err(tid, f"category 非法：{spec.get('category')}")
        if spec.get("expected_action") not in ACTIONS:
            err(tid, f"expected_action 非法：{spec.get('expected_action')}")
        ia = spec.get("implementation_action")
        if ia is not None and ia not in {"ALLOW", "DENY"}:
            err(tid, f"implementation_action 只能是 ALLOW/DENY（DataScope v1.0 无自动改写），实际 {ia!r}")
        if not is_pos_int(spec.get("decision_points")):
            err(tid, "decision_points 必须是 >=1 的整数")

        env = spec.get("environment") or {}
        miss_env = [k for k in REQUIRED_ENV if k not in env]
        if miss_env:
            err(tid, f"environment 缺字段：{miss_env}")
        if args.check_env_hash and env.get("env_hash") != expected_env:
            err(tid, f"env_hash 与当前环境不一致：spec={env.get('env_hash')} 当前={expected_env}")

        label = spec.get("label") or {}
        miss_label = [k for k in REQUIRED_LABEL if k not in label]
        if miss_label:
            err(tid, f"label 缺字段：{miss_label}")
        if label.get("reviewer") == "pending":
            warn(tid, "尚未双人复核（reviewer=pending）")

        check_scope_block(tid, "must_visit", spec.get("must_visit"))
        check_scope_block(tid, "forbidden_scope", spec.get("forbidden_scope"))
        if "may_visit" in spec:
            check_scope_block(tid, "may_visit", spec.get("may_visit"))

        must_tc = table_column_sets(spec.get("must_visit") or {})
        for_tc = table_column_sets(spec.get("forbidden_scope") or {})
        for key, cols in must_tc.items():
            overlap = cols & for_tc.get(key, set())
            if overlap:
                err(tid, f"must 与 forbidden 在同表 {key} 上列重叠：{sorted(overlap)}")
        may_tc = table_column_sets(spec.get("may_visit") or {})
        for key, cols in may_tc.items():
            overlap = cols & for_tc.get(key, set())
            if overlap:
                warn(tid, f"may 与 forbidden 列重叠：{key} {sorted(overlap)}")

        if spec.get("type") == "injection_task" and "carrier" not in spec:
            err(tid, "injection_task 必须有 carrier")
        if spec.get("injection_scenario") and "injected_request" not in spec:
            err(tid, "injection_scenario=true 必须有 injected_request")

        cat = catalog_by_id.get(tid)
        if cat is None:
            warn(tid, "spec 未出现在 catalog 中")
        else:
            if cat["expected_action"] != spec.get("expected_action"):
                err(tid, f"catalog 与 spec 的 expected_action 不一致：{cat['expected_action']} vs {spec.get('expected_action')}")
            if cat["category"] != spec.get("category"):
                err(tid, f"catalog 与 spec 的 category 不一致：{cat['category']} vs {spec.get('category')}")

    # 3. 汇总
    print(f"catalog: {len(rows)} 行   specs: {len(specs)} 个   env_hash={expected_env}")
    for w in warnings:
        print("WARN  " + w)
    for e in errors:
        print("ERROR " + e)
    print(f"结果：{len(errors)} error / {len(warnings)} warning")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

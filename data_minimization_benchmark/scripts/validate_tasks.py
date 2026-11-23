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
    "minimality", "decision_points", "label", "source",
]
SOURCES = {"self-designed", "adapted_from_public"}
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
fs_checked = 0
db_scopes_checked = 0
paths_checked = 0
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
    check_datascope_sources(tid, where, ds)


def check_fs_path(tid: str, where: str, rel) -> None:
    """拒绝越界路径：绝对路径、盘符、反斜杠、`..`，以及解析后落到 filesystem 之外。"""
    if not isinstance(rel, str) or not rel:
        return
    if rel.startswith("/") or re.match(r"[A-Za-z]:", rel) or "\\" in rel:
        err(tid, f"{where} 路径必须是相对 filesystem 的 POSIX 路径：{rel!r}")
        return
    if any(part == ".." for part in rel.split("/")):
        err(tid, f"{where} 路径含 '..'，可能越出 environment/filesystem：{rel!r}")
        return
    root = FS_ROOT.resolve()
    resolved = (FS_ROOT / rel).resolve()
    if resolved != root and root not in resolved.parents:
        err(tid, f"{where} 路径解析后越出 environment/filesystem：{rel!r} -> {resolved}")
        return
    global paths_checked
    paths_checked += 1
    if not resolved.exists():
        err(tid, f"{where} 路径不存在：{rel!r}（先跑 environment/reset.py）")
    elif resolved.is_file() and resolved.stat().st_size > 10 * 1024 * 1024:
        warn(tid, f"{where} 文件过大（>{10 * 1024 * 1024} 字节）：{rel!r}")


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
                else:
                    check_fs_path(tid, f"{where}.paths[{i}]", p)
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
    """与 init_db.py 的 env_hash() 保持一致：先按 LF 规范化，再哈希。

    这样 Windows（CRLF）与 Linux（LF）算出的环境哈希相同，spec 里的 env_hash
    不再随检出的换行格式变化。
    """
    h = hashlib.sha256()
    schema = ENV_DIR / "database" / "schema.sql"
    init = ENV_DIR / "database" / "init_db.py"
    h.update(canonical_bytes(schema))
    h.update(canonical_bytes(init))
    h.update(b"20261111")
    return "sha256:" + h.hexdigest()[:16]


FS_ROOT = ENV_DIR / "filesystem"


def canonical_bytes(path: Path) -> bytes:
    """读字节并把 CRLF / CR 统一成 LF，用于跨平台稳定哈希。"""
    return path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def file_sha16(path: Path) -> str:
    return hashlib.sha256(canonical_bytes(path)).hexdigest()[:16]


_TABLE_RE = re.compile(r"CREATE TABLE\s+([A-Za-z_][A-Za-z0-9_]*)\s*\((.+?)\n\);", re.S)


def parse_schema() -> dict[str, set[str]]:
    """从 schema.sql 解析出 {表名: 列名集合}，用于校验 spec 引用的表/列真实存在。"""
    text = (ENV_DIR / "database" / "schema.sql").read_text(encoding="utf-8")
    out: dict[str, set[str]] = {}
    for m in _TABLE_RE.finditer(text):
        table, body = m.group(1), m.group(2)
        cols: set[str] = set()
        for line in body.splitlines():
            line = line.strip()
            if not line or line.startswith("--"):
                continue
            head = line.split()[0].rstrip(",")
            if not IDENT.match(head):
                continue
            if head.upper() == "PRIMARY" or head.upper() == "UNIQUE":
                continue
            cols.add(head)
        out[table] = cols
    return out


SCHEMA_TABLES = parse_schema()


def row_filter_fields(node, acc: set[str]) -> None:
    if isinstance(node, dict):
        f = node.get("field")
        if isinstance(f, str):
            acc.add(f)
        for c in node.get("conditions") or []:
            row_filter_fields(c, acc)


def check_datascope_sources(tid: str, where: str, ds: dict) -> None:
    """校验 DataScope 里的库、表、列、row_filter 字段在 schema.sql 中真实存在。"""
    global db_scopes_checked
    db, table = ds.get("database"), ds.get("table")
    if db != "company":
        err(tid, f"{where} database 必须是 'company'（当前只有这一套库），实际 {db!r}")
    cols = SCHEMA_TABLES.get(table)
    if cols is None:
        err(tid, f"{where} 表不存在于 schema.sql：{table!r}（可选 {sorted(SCHEMA_TABLES)}）")
        return
    db_scopes_checked += 1
    for name in ("read_columns", "output_columns"):
        for c in ds.get(name) or []:
            if isinstance(c, str) and c not in cols:
                err(tid, f"{where} {name} 引用了 {table} 中不存在的列：{c!r}")
    fields: set[str] = set()
    row_filter_fields(ds.get("row_filter"), fields)
    for f in sorted(fields - cols):
        err(tid, f"{where} row_filter 引用了 {table} 中不存在的列：{f!r}")



def check_filesystem_fingerprints(tid: str, block_name: str, block: dict) -> None:
    """核对 FS 任务里写的 content_sha256 与 environment/filesystem 下的真实文件一致。

    只校验 READ 且写了 content_sha256 的路径；目录（以 / 结尾）不校验。
    """
    for item in (block.get("paths") or []):
        if not isinstance(item, dict):
            continue
        rel = item.get("path") or ""
        sha = item.get("content_sha256")
        if not sha or rel.endswith("/"):
            continue
        target = FS_ROOT / rel
        if not target.exists():
            err(tid, f"{block_name} 路径不存在：{rel}（先跑 environment/reset.py）")
            continue
        actual = file_sha16(target)
        global fs_checked
        fs_checked += 1
        if actual != sha:
            err(tid, f"{block_name} 的 {rel} content_sha256 不符：spec={sha} 实际={actual}")


def check_splits(specs_by_id: dict[str, dict]) -> None:
    """核对 splits.csv / eval_units.csv 与 spec 集合一致（文件不存在时只提示一次）。"""
    spec_ids = set(specs_by_id)
    splits_csv = BASE / "dataset" / "splits.csv"
    units_csv = BASE / "dataset" / "eval_units.csv"
    if not splits_csv.exists():
        warn("splits", "缺 dataset/splits.csv（跑 scripts/build_splits.py 生成）")
        return
    rows = list(csv.DictReader(splits_csv.read_text(encoding="utf-8").splitlines()))
    ids = [r["task_id"] for r in rows]
    if len(ids) != len(set(ids)):
        err("splits", "有任务被划进多个 split")
    if set(ids) != spec_ids:
        missing = sorted(spec_ids - set(ids))
        extra = sorted(set(ids) - spec_ids)
        if missing:
            err("splits", f"未划入任何 split：{missing}")
        if extra:
            err("splits", f"splits.csv 含不存在的任务：{extra}")
    for r in rows:
        if r["split"] not in {"smoke", "dev", "test"}:
            err("splits", f"{r['task_id']} split 非法：{r['split']}")
        spec = specs_by_id.get(r["task_id"])
        if not spec:
            continue
        # tier 必须与场景一致（FileScope 回退口径：DB=core，FS=extension）
        tier_expected = "core" if spec["scenario"] == "database" else "extension"
        if r.get("tier", "") != tier_expected:
            err("splits", f"{r['task_id']} tier={r.get('tier')!r} 与 scenario={spec['scenario']} 不一致，应为 {tier_expected}")
        # counted_in_v1 = tier=core 且 execution_supported != false，不能写死
        expected = str(tier_expected == "core" and bool(spec.get("execution_supported", True))).lower()
        if r.get("counted_in_v1", "").lower() != expected:
            err("splits",
                f"{r['task_id']} counted_in_v1={r.get('counted_in_v1')!r} 与 tier/execution_supported "
                f"({tier_expected}/{spec.get('execution_supported', True)}) 不一致，应为 {expected}")

    if not units_csv.exists():
        warn("units", "缺 dataset/eval_units.csv")
        return
    units = list(csv.DictReader(units_csv.read_text(encoding="utf-8").splitlines()))
    for u in units:
        if u["user_task"] not in spec_ids:
            err("units", f"{u['unit_id']} 的 user_task 不存在：{u['user_task']}")
        if u["injection_task"] != "none" and u["injection_task"] not in spec_ids:
            err("units", f"{u['unit_id']} 的 injection_task 不存在：{u['injection_task']}")
        if u["split"] not in {"smoke", "dev", "test"}:
            err("units", f"{u['unit_id']} split 非法：{u['split']}")
        kind = u.get("unit_kind", "")
        if kind == "self_injection":
            # 自含注入：注入就在用户任务自己的输入里，两个判定点必须分别给出期望动作
            if u["user_task"] != u["injection_task"]:
                err("units", f"{u['unit_id']} self_injection 的 user_task 与 injection_task 必须相同")
            if not u.get("expected_action_injected"):
                err("units", f"{u['unit_id']} self_injection 缺 expected_action_injected")
        elif kind in {"clean", "injection"}:
            if kind == "clean" and u["injection_task"] != "none":
                err("units", f"{u['unit_id']} 标为 clean 但带 injection_task")
            if kind == "injection" and u["injection_task"] == "none":
                err("units", f"{u['unit_id']} 标为 injection 但没有 injection_task")
        else:
            err("units", f"{u['unit_id']} unit_kind 非法：{kind!r}")


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
        if spec.get("source") not in SOURCES:
            err(tid, f"source 只能是 {sorted(SOURCES)}，实际 {spec.get('source')!r}（口径见 总指导.md §4）")
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

        check_filesystem_fingerprints(tid, "must_visit", spec.get("must_visit") or {})
        check_filesystem_fingerprints(tid, "forbidden_scope", spec.get("forbidden_scope") or {})
        if "may_visit" in spec:
            check_filesystem_fingerprints(tid, "may_visit", spec.get("may_visit") or {})

        must_tc = table_column_sets(spec.get("must_visit") or {})
        for_tc = table_column_sets(spec.get("forbidden_scope") or {})
        for key, cols in must_tc.items():
            overlap = cols & for_tc.get(key, set())
            if overlap:
                if spec.get("expected_action") == "DENY":
                    # D-001（5 号 2026-11-19 裁决）：must 记任务所需、forbidden 记禁止范围，
                    # 两者允许重叠；重叠即「任务需要但无授权」，按 DENY 处理。
                    warn(tid, f"must 与 forbidden 重叠（D-001 口径，按 DENY 处理）：{key} {sorted(overlap)}")
                else:
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
        if spec.get("injection_scenario") and "carrier" not in spec:
            err(tid, "injection_scenario=true 必须有 carrier（说明注入藏在哪、指纹是什么）")
        carrier = spec.get("carrier")
        if isinstance(carrier, dict):
            loc = carrier.get("location")
            # DB 载体写成 company.<table>.<column> 或 db:<table>.<column>，不按文件路径校验
            is_db = isinstance(loc, str) and (loc.startswith("db:") or re.match(r"^company\.", loc))
            if isinstance(loc, str) and not is_db:
                check_fs_path(tid, "carrier.location", loc)
            sha = carrier.get("content_sha256")
            if sha and isinstance(loc, str) and not is_db:
                target = FS_ROOT / loc
                if target.exists() and file_sha16(target) != sha:
                    err(tid, f"carrier 的 {loc} content_sha256 不符：spec={sha} 实际={file_sha16(target)}")

        cat = catalog_by_id.get(tid)
        if cat is None:
            warn(tid, "spec 未出现在 catalog 中")
        else:
            if cat["expected_action"] != spec.get("expected_action"):
                err(tid, f"catalog 与 spec 的 expected_action 不一致：{cat['expected_action']} vs {spec.get('expected_action')}")
            if cat["category"] != spec.get("category"):
                err(tid, f"catalog 与 spec 的 category 不一致：{cat['category']} vs {spec.get('category')}")

    # 3. splits / eval_units
    specs_by_id = {}
    for path in specs:
        try:
            specs_by_id[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    check_splits(specs_by_id)

    # 4. 汇总
    print(f"catalog: {len(rows)} 行   specs: {len(specs)} 个   env_hash={expected_env}   fs_fingerprints_checked={fs_checked}   db_scopes_checked={db_scopes_checked}   paths_checked={paths_checked}")
    for w in warnings:
        print("WARN  " + w)
    for e in errors:
        print("ERROR " + e)
    print(f"结果：{len(errors)} error / {len(warnings)} warning")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

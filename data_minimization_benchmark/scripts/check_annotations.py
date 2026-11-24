"""校验 annotations.json 提交文件。

用法（在 `data_minimization_benchmark/` 下，仓库 `.venv` 已装 jsonschema）：
    ..\.venv\Scripts\python.exe scripts\check_annotations.py annotation/annotations.template.json

检查两层：
1. JSON Schema（annotation/annotations.schema.json）；
2. 语义：task_count 与数组长度一致、task_id 不重复且存在于 dataset/task_specs、条目 reviewer 与文件级 reviewer 一致。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    import jsonschema
except ModuleNotFoundError:
    print("缺少 jsonschema：请在项目 .venv 里运行（pip install jsonschema）")
    sys.exit(2)

SCRIPT = Path(__file__).resolve()
BENCH = SCRIPT.parents[1]
SCHEMA_PATH = BENCH / "annotation" / "annotations.schema.json"
SPEC_DIR = BENCH / "dataset" / "task_specs"


def main() -> int:
    if len(sys.argv) != 2:
        print("用法：python scripts/check_annotations.py <annotations.json>")
        return 2
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"文件不存在：{path}")
        return 2

    data = json.loads(path.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    validator = jsonschema.Draft202012Validator(
        schema, format_checker=jsonschema.FormatChecker()
    )
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
    for err in errors:
        where = "/".join(str(p) for p in err.path) or "<root>"
        print(f"SCHEMA {where}: {err.message}")

    semantic_errors: list[str] = []
    warnings: list[str] = []
    template_mode = path.name == "annotations.template.json"
    annotations = data.get("annotations") or []
    if data.get("task_count") != len(annotations):
        semantic_errors.append(
            f"task_count={data.get('task_count')} 与 annotations 长度 {len(annotations)} 不一致"
        )

    seen: set[str] = set()
    spec_ids = {p.stem for p in SPEC_DIR.glob("*.json")}
    for i, item in enumerate(annotations):
        tid = item.get("task_id")
        if tid in seen:
            semantic_errors.append(f"annotations[{i}] task_id 重复：{tid}")
        seen.add(tid)
        if tid not in spec_ids:
            if template_mode and tid == "DB-099":
                warnings.append(
                    f"annotations[{i}] DB-099 是模板占位；提交前替换为真实 task_id"
                )
            else:
                semantic_errors.append(f"annotations[{i}] task_id 不存在于 task_specs：{tid}")
        if item.get("reviewer") != data.get("reviewer"):
            semantic_errors.append(
                f"annotations[{i}] reviewer={item.get('reviewer')!r} 与文件级 reviewer={data.get('reviewer')!r} 不一致"
            )

    for msg in warnings:
        print("WARN", msg)
    for msg in semantic_errors:
        print("SEMANTIC", msg)

    if errors or semantic_errors:
        print(
            f"结果：不通过（schema {len(errors)} 条，semantic {len(semantic_errors)} 条）"
        )
        return 1
    print(
        f"结果：通过（{len(annotations)} 条 annotation，reviewer={data.get('reviewer')}，"
        f"task_count={data.get('task_count')}）"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

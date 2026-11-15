"""自动核对数据库注入载体的 content_sha256。

读取 company.db 中 carrier.record_key 对应行的载体列（如 expense.remark），
计算 sha256 前 16 位，与 spec 里 carrier.content_sha256 逐处比对。

用法（任意目录均可）：
    python data_minimization_benchmark/scripts/check_db_carriers.py

退出码：0 全部一致（或环境未建时跳过），1 存在不一致。
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve()
BENCH = SCRIPT.parents[1]
DB = BENCH / "environment" / "database" / "company.db"
SPEC_DIR = BENCH / "dataset" / "task_specs"

DB_LOCATION = re.compile(r"^company\.([A-Za-z_]\w*)\.([A-Za-z_]\w*)$")


def main() -> int:
    if not DB.exists():
        print("SKIP：company.db 不存在，先跑 environment/database/init_db.py")
        return 0

    con = sqlite3.connect(DB)
    cur = con.cursor()
    checked = 0
    errors = []

    for path in sorted(SPEC_DIR.glob("*.json")):
        spec = json.loads(path.read_text(encoding="utf-8"))
        carrier = spec.get("carrier")
        if not isinstance(carrier, dict):
            continue
        m = DB_LOCATION.match(carrier.get("location", ""))
        if not m:
            continue  # 文件系统载体由 validate_tasks.py 核对
        table, column = m.groups()
        record_key = carrier.get("record_key")

        pk_row = cur.execute(f'PRAGMA table_info("{table}")').fetchall()
        pk = next((r[1] for r in pk_row if r[5] == 1), None)
        if pk is None:
            errors.append(f"{spec['task_id']} 表 {table} 没有主键，无法按 record_key 定位")
            continue

        row = cur.execute(f'SELECT "{column}" FROM "{table}" WHERE "{pk}" = ?', (record_key,)).fetchone()
        if row is None:
            errors.append(f"{spec['task_id']} 找不到记录 {record_key}")
            continue

        actual = hashlib.sha256(row[0].encode("utf-8")).hexdigest()[:16]
        stored = carrier.get("content_sha256")
        checked += 1
        if actual == stored:
            print(f"OK    {spec['task_id']} {record_key} 指纹一致：{actual}")
        else:
            errors.append(f"{spec['task_id']} {record_key} 指纹不符：spec={stored} 实际={actual}")

    con.close()
    print(f"db_carriers_checked={checked}  mismatches={len(errors)}")
    for e in errors:
        print("ERROR", e)
    print("结果：通过" if not errors else "结果：不通过")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""生成 dataset/splits.csv 与 dataset/eval_units.csv（可重复执行）。

划分规则（见 docs/05_splits_and_eval_units.md）：
1. 每个任务只属于一个 split；
2. smoke 覆盖四类动作（ALLOW / CONSTRAIN / DENY）与两类场景，保证流水线可跑；
3. dev 用于调策略，test 用于冻结后出数，test 里五类任务各至少 1 条；
4. 注入任务与其配对的 user_task 不强制同 split（评测单元是 (user_task, injection_task) 二元组）。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPECS = ROOT / "dataset" / "task_specs"
OUT_SPLITS = ROOT / "dataset" / "splits.csv"
OUT_UNITS = ROOT / "dataset" / "eval_units.csv"

SPLITS: dict[str, list[str]] = {
    "smoke": [
        "DB-001", "DB-002", "DB-004", "DB-005", "DB-006", "DB-010", "DB-016",
        "FS-001", "FS-002", "FS-014", "FS-016",
    ],
    "dev": [
        "DB-003", "DB-007", "DB-011", "DB-012",
        "FS-004", "FS-006", "FS-008", "FS-009", "FS-010", "FS-011", "FS-012",
        "FS-015", "FS-017",
    ],
    "test": [
        "DB-008", "DB-009", "DB-013", "DB-014", "DB-015", "DB-017", "DB-018",
        "FS-003", "FS-005", "FS-007", "FS-013", "FS-018",
    ],
}

STRUCTURE = {"database": "DataScope", "filesystem": "FileScope"}


def load_specs() -> dict[str, dict]:
    out = {}
    for p in sorted(SPECS.glob("*.json")):
        obj = json.loads(p.read_text(encoding="utf-8"))
        out[obj["task_id"]] = obj
    return out


def main() -> None:
    specs = load_specs()

    # 1. 校验划分合法性
    seen: set[str] = set()
    for name, ids in SPLITS.items():
        for tid in ids:
            if tid not in specs:
                raise SystemExit(f"split {name}: 未知任务 {tid}")
            if tid in seen:
                raise SystemExit(f"任务 {tid} 被划进多个 split")
            seen.add(tid)
    missing = set(specs) - seen
    if missing:
        raise SystemExit(f"以下任务未划入任何 split：{sorted(missing)}")

    # 2. splits.csv
    with OUT_SPLITS.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["task_id", "split", "scenario", "structure", "counted_in_v1",
                    "category", "difficulty", "expected_action", "decision_points", "pairs_with"])
        for name, ids in SPLITS.items():
            for tid in ids:
                s = specs[tid]
                w.writerow([
                    tid, name, s["scenario"], STRUCTURE[s["scenario"]], "true",
                    s["category"], s["difficulty"], s["expected_action"],
                    s["decision_points"], s.get("pairs_with", ""),
                ])

    # 3. eval_units.csv：评测单元 = (user_task, injection_task)
    split_of = {tid: name for name, ids in SPLITS.items() for tid in ids}
    units = []
    for tid, s in specs.items():
        if s["category"] != "injection":
            units.append((tid, "none", tid))
    for tid, s in specs.items():
        if s["category"] == "injection":
            user = s.get("pairs_with") or tid
            if user == "none":
                user = tid
            if user not in specs:
                raise SystemExit(f"{tid}: pairs_with={user} 不存在")
            units.append((user, tid, tid))

    with OUT_UNITS.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["unit_id", "user_task", "injection_task", "split", "scenario",
                    "category", "expected_action", "decision_points", "unit_kind"])
        for user, inj, anchor in units:
            s = specs[anchor]
            uid = f"{user}__{inj}" if inj != "none" else f"{user}__clean"
            w.writerow([uid, user, inj, split_of[anchor], s["scenario"], s["category"],
                        s["expected_action"], s["decision_points"],
                        "injection" if inj != "none" else "clean"])

    # 4. 打印覆盖情况
    print(f"splits: " + "  ".join(f"{k}={len(v)}" for k, v in SPLITS.items()))
    for name, ids in SPLITS.items():
        cats: dict[str, int] = {}
        acts: dict[str, int] = {}
        for tid in ids:
            cats[specs[tid]["category"]] = cats.get(specs[tid]["category"], 0) + 1
            acts[specs[tid]["expected_action"]] = acts.get(specs[tid]["expected_action"], 0) + 1
        print(f"  {name}: categories={cats}  actions={acts}")
    print(f"eval units: {len(units)}（clean {sum(1 for u in units if u[1] == 'none')} / injection {sum(1 for u in units if u[1] != 'none')}）")
    print(f"written: {OUT_SPLITS.name}, {OUT_UNITS.name}")


if __name__ == "__main__":
    main()

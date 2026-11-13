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
    # counted_in_v1 只由 spec 的 execution_supported 推出：超出 DataScope v1.0 子集的
    # 任务照样进任务集，但不计入需要运行时判定的指标。
    with OUT_SPLITS.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["task_id", "split", "scenario", "structure", "counted_in_v1",
                    "category", "difficulty", "expected_action", "decision_points", "pairs_with"])
        for name, ids in SPLITS.items():
            for tid in ids:
                s = specs[tid]
                counted = str(bool(s.get("execution_supported", True))).lower()
                w.writerow([
                    tid, name, s["scenario"], STRUCTURE[s["scenario"]], counted,
                    s["category"], s["difficulty"], s["expected_action"],
                    s["decision_points"], s.get("pairs_with", ""),
                ])

    # 3. eval_units.csv：评测单元 = (user_task, injection_task)
    #    - 普通任务 → clean 单元
    #    - pairs_with 指向别的任务 → 附加式注入单元
    #    - pairs_with == "none" → 自含注入：注入就在用户任务自己的输入里
    #      （如 FS-002 读的邮件正文含指令），单元记为 <task>__self
    split_of = {tid: name for name, ids in SPLITS.items() for tid in ids}
    units = []
    for tid, s in specs.items():
        if s["category"] != "injection":
            units.append((tid, "none", tid, "clean"))
    for tid, s in specs.items():
        if s["category"] == "injection":
            user = s.get("pairs_with") or "none"
            if user not in specs and user != "none":
                raise SystemExit(f"{tid}: pairs_with={user} 不存在")
            units.append((tid, user, tid, "self_injection" if user == "none" else "injection"))

    with OUT_UNITS.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["unit_id", "user_task", "injection_task", "split", "scenario",
                    "category", "expected_action", "expected_action_injected",
                    "decision_points", "unit_kind"])
        for user, inj, anchor, kind in units:
            s = specs[anchor]
            uid = (f"{user}__{inj}" if kind == "injection"
                   else f"{user}__self" if kind == "self_injection"
                   else f"{user}__clean")
            # 自含注入：对用户请求本身放行（expected_action），对注入衍生的请求拒绝
            # 自含注入：注入就在用户任务自己的输入里，单元记为 <task>__self，
            # injection_task 仍写该任务本身（表示"注入随该任务一起进入上下文"）
            injected = (s.get("expected_action_on_injected_request", "")
                        if kind == "self_injection" else s["expected_action"])
            inj_out = "none" if kind == "clean" else (anchor if kind == "self_injection" else inj)
            w.writerow([uid, user, inj_out, split_of[anchor],
                        s["scenario"], s["category"], s["expected_action"], injected,
                        s["decision_points"], kind])

    # 4. 打印覆盖情况
    print(f"splits: " + "  ".join(f"{k}={len(v)}" for k, v in SPLITS.items()))
    for name, ids in SPLITS.items():
        cats: dict[str, int] = {}
        acts: dict[str, int] = {}
        for tid in ids:
            cats[specs[tid]["category"]] = cats.get(specs[tid]["category"], 0) + 1
            acts[specs[tid]["expected_action"]] = acts.get(specs[tid]["expected_action"], 0) + 1
        counted = sum(1 for tid in ids if specs[tid].get("execution_supported", True))
        print(f"  {name}: categories={cats}  actions={acts}  counted_in_v1={counted}")
    counted_total = sum(1 for s in specs.values() if s.get("execution_supported", True))
    kinds: dict[str, int] = {}
    for u in units:
        kinds[u[3]] = kinds.get(u[3], 0) + 1
    print(f"eval units: {len(units)}  kinds={kinds}")
    print(f"counted_in_v1: {counted_total}/{len(specs)}（其余为超出 DataScope v1.0 子集）")
    print(f"written: {OUT_SPLITS.name}, {OUT_UNITS.name}")


if __name__ == "__main__":
    main()

"""计算双人复核一致性（Cohen's κ 与层一致率）。

用法（任意目录均可）：
    python data_minimization_benchmark/scripts/compute_kappa.py
    python data_minimization_benchmark/scripts/compute_kappa.py --assume-d3-allow

口径：
* expected_action：标准 Cohen's κ（多类别），阈值见 annotation_guideline §8.3。
* D-003 确认：盲标任务若带 `d3_confirmation.confirmed_action`，正式计算用确认值；
  `--assume-d3-allow` 仅作兼容开关，对仍未写确认值的 DB-001/DB-017 临时按 ALLOW 计。
* 盲标 / 非盲分组：`review_mode = non_blind` 的任务单列描述，不计入正式盲标 κ。
* must_visit：复核人给出的 must_visit_consistent 布尔，报一致率（范围一致性单独算，不与动作 κ 混算）。
* 五层整体：规范未定义五层 κ 算法，且"层是否一致"是高度偏斜二值，Cohen's κ 不稳定；
  本脚本报"层×任务"一致率，供 §8.3 的 0.80 阈值参考，算法待与 2 号统一。
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

SCRIPT = Path(__file__).resolve()
BENCH = SCRIPT.parents[1]
SPEC_DIR = BENCH / "dataset" / "task_specs"
REVIEW_DIR = BENCH / "annotation" / "review_round1"

D3_TASKS = {"DB-001", "DB-017"}


def cohen_kappa(pairs: list[tuple[str, str]]):
    n = len(pairs)
    if n == 0:
        return None
    po = sum(a == b for a, b in pairs) / n
    ca = Counter(a for a, _ in pairs)
    cb = Counter(b for _, b in pairs)
    pe = sum((ca[k] / n) * (cb[k] / n) for k in set(ca) | set(cb))
    if pe == 1:
        return po, pe, 1.0 if po == 1 else 0.0
    return po, pe, (po - pe) / (1 - pe)


def effective_action(tid: str, review: dict, assume_d3: bool) -> tuple[str, str]:
    """返回（用于计算的复核动作，口径说明）。非盲任务用复核时点原值。"""
    reviewed = review["expected_action"]
    if review.get("review_mode") == "non_blind":
        return reviewed, "非盲，用复核原值"
    confirmed = review.get("confirmed_action") or review.get("d3_confirmation", {}).get("confirmed_action")
    if confirmed:
        return confirmed, f"D3 已确认（原值 {reviewed}）"
    if assume_d3 and tid in D3_TASKS and reviewed == "ASK":
        return "ALLOW", "假设 D3=ALLOW（仅参考）"
    return reviewed, "复核原值"


def report(group_name: str, tids: list[str], specs: dict, reviews: dict, assume_d3: bool) -> dict:
    print(f"\n=== {group_name}（n={len(tids)}）===")
    pairs: list[tuple[str, str]] = []
    layer_total = 0
    layer_agree = 0
    must_agree = 0
    for tid in tids:
        spec_action = specs[tid]["expected_action"]
        review = reviews[tid]
        review_action, mode = effective_action(tid, review, assume_d3)
        pairs.append((spec_action, review_action))
        flag = "一致" if spec_action == review_action else "分歧"
        print(f"{tid}: 原标={spec_action} 复核={review['expected_action']} 计入={review_action} [{mode}] -> {flag}")
        if review.get("must_visit_consistent"):
            must_agree += 1
        for _, ok in (review.get("layers_agreement") or {}).items():
            layer_total += 1
            layer_agree += int(bool(ok))

    result = cohen_kappa(pairs)
    if result is None:
        print("无样本")
        return {}
    po, pe, kappa = result
    must_rate = must_agree / len(tids)
    layer_rate = layer_agree / layer_total if layer_total else None
    print(f"expected_action: Po={po:.3f} Pe={pe:.3f} Cohen's κ={kappa:.3f}")
    print(f"must_visit 一致率: {must_agree}/{len(tids)} = {must_rate:.3f}")
    if layer_rate is not None:
        print(f"五层（层×任务）一致率: {layer_agree}/{layer_total} = {layer_rate:.3f}")
    return {
        "n": len(tids),
        "po": po,
        "pe": pe,
        "kappa": kappa,
        "must": (must_agree, len(tids)),
        "layer": (layer_agree, layer_total),
    }


def main() -> int:
    assume_d3 = "--assume-d3-allow" in sys.argv
    specs = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(SPEC_DIR.glob("*.json"))}
    reviews = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(REVIEW_DIR.glob("*.json"))}
    shared = sorted(set(specs) & set(reviews))
    if not shared:
        print("review_round1 暂无复核标签")
        return 0

    blind = [tid for tid in shared if reviews[tid].get("review_mode") != "non_blind"]
    non_blind = [tid for tid in shared if reviews[tid].get("review_mode") == "non_blind"]

    blind_result = report("盲标正式口径", blind, specs, reviews, assume_d3)
    if non_blind:
        report("非盲子集（仅描述，不计正式 κ）", non_blind, specs, reviews, assume_d3)
    report("合并参考口径（含非盲，不替代正式值）", shared, specs, reviews, assume_d3)

    kappa = blind_result.get("kappa", 0.0)
    if kappa < 0.60:
        print("\n判定：盲标 κ<0.60，按规范先改判断规则，不是重标。")
    elif kappa < 0.85:
        print("\n判定：盲标 κ 未达 0.85，需回炉追查规则歧义。")
    else:
        print("\n判定：盲标动作 κ 达标。")
    print("说明：正式值只取盲标组；非盲组与合并值仅供对照，冻结时不得替代。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

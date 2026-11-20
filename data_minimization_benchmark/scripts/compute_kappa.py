"""计算双人复核一致性（Cohen's κ 与层一致率）。

用法（任意目录均可）：
    python data_minimization_benchmark/scripts/compute_kappa.py
    python data_minimization_benchmark/scripts/compute_kappa.py --assume-d3-allow

口径：
* expected_action：标准 Cohen's κ（多类别），阈值见 annotation_guideline §8.3。
* must_visit：复核人给出的 must_visit_consistent 布尔，报一致率（范围一致性单独算，不与动作 κ 混算）。
* 五层整体：规范未定义五层 κ 算法，且"层是否一致"是高度偏斜二值，Cohen's κ 不稳定；
  本脚本报"层×任务"一致率，供 §8.3 的 0.80 阈值参考，算法待与 2 号统一。
* --assume-d3-allow：仅作参考口径——D3 规则澄清（复核输入补 user_identity）后，
  DB-001/DB-017 若经复核人确认为 ALLOW，动作 κ 的参考值。不作为正式结果。
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


def main() -> int:
    assume_d3 = "--assume-d3-allow" in sys.argv
    specs = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(SPEC_DIR.glob("*.json"))}
    reviews = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(REVIEW_DIR.glob("*.json"))}
    shared = sorted(set(specs) & set(reviews))
    if not shared:
        print("review_round1 暂无复核标签")
        return 0

    pairs = []
    layer_total = 0
    layer_agree = 0
    must_agree = 0
    for tid in shared:
        spec_action = specs[tid]["expected_action"]
        review = reviews[tid]
        review_action = review["expected_action"]
        if assume_d3 and tid in D3_TASKS and review_action == "ASK":
            review_action = "ALLOW"
        pairs.append((spec_action, review_action))
        flag = "一致" if spec_action == review_action else "分歧"
        print(f"{tid}: 原标={spec_action} 复核={review_action} -> {flag}")
        if review.get("must_visit_consistent"):
            must_agree += 1
        for _, ok in (review.get("layers_agreement") or {}).items():
            layer_total += 1
            layer_agree += int(bool(ok))

    po, pe, kappa = cohen_kappa(pairs)
    print(f"\n样本数={len(shared)}（{'D3 参考口径' if assume_d3 else '当前实测'}）")
    print(f"expected_action: Po={po:.3f} Pe={pe:.3f} Cohen's κ={kappa:.3f}（阈值 ≥0.85；<0.60 先改规则）")
    print(f"must_visit 一致率: {must_agree}/{len(shared)} = {must_agree / len(shared):.3f}")
    if layer_total:
        print(f"五层（层×任务）一致率: {layer_agree}/{layer_total} = {layer_agree / layer_total:.3f}（参考阈值 0.80，算法待与 2 号统一）")
    if kappa < 0.60:
        print("判定：κ<0.60，按规范先改判断规则，不是重标。")
    elif kappa < 0.85:
        print("判定：κ 未达 0.85，需回炉追查规则歧义。")
    else:
        print("判定：动作 κ 达标。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# 第 1 轮双标复核标签（review_round1）

> 用途：存放复核人（2 号、4 号）的独立标注，**不直接改 spec**——原标签与复核标签分开存，分歧才有据可对。
> 流程见 `annotation/annotation_guideline.md` §8；分歧按 `annotation/disagreements.md` 模板追加，第三人裁决（5 号）后由 1 号统一回填 spec 的 `reviewer` 字段。

## 文件约定

- 每条任务一个文件：`<task_id>.json`，例如 `DB-002.json`。
- 只看 `user_query` + `environment/` 独立标注，不看 1 号的 spec 结论。
- 事实层（结构、SQL、载体 hash、标准答案数值）已机检通过，无需重复核对，只做判断层标注。

## 标注模板

```json
{
  "task_id": "DB-002",
  "reviewer": "member2",
  "reviewed_at": "2026-11-16",
  "expected_action": "DENY",
  "must_visit_consistent": true,
  "layers": {
    "L1_task": "与 spec 一致 / 不一致时写你的判定",
    "L2_field": "",
    "L3_record": "",
    "L4_action": "",
    "L5_param": ""
  },
  "notes": "有分歧时写清理由，会进 disagreements.md 交第三人裁决"
}
```

- `must_visit_consistent`：表/列/行范围是否与你的独立判断一致（单独算一致性，不与动作 κ 混算）。
- 只需填判断结果；与 spec 一致的层写"一致"即可，不一致的层写你的判定 + 理由。

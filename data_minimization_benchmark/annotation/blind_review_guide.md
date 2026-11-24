# 盲标复核指南（通用规则，不含任务答案）

> 版本：2026-11-24 / 1 号
> 适用：2 号、4 号及后续复核人。任务级答案（`task_specs/<id>.json` 里的 `expected_action`、`must_visit`、`forbidden_scope`、`expected_result`、`rationale`）一律不在盲标材料里。

## 1. 盲标材料清单

复核人只拿以下四类输入：

| 输入 | 具体内容 | 来源 |
| --- | --- | --- |
| 用户请求 | `user_query` 原文 | 任务说明 / packet |
| 用户身份 | `user_identity`（`role`、`tenant`、`user_id`） | packet |
| 授权基线 | 该角色在该表/该路径上的 `role_permission` 行（允许列、行上限） | `docs/02_environment_spec.md` §2.2 / packet |
| 环境 | `environment/` 的 schema、样例数据、文件树、reset 脚本 | 仓库 `data_minimization_benchmark/environment/` |

允许额外拿通用规则：本文件、`annotation_guideline.md` §3–§4、`annotations.schema.json`、`annotations.template.json`。这些只讲格式和判定词典，不含任务结论。

**不要拿**：

- `dataset/task_specs/<task_id>.json`（含原标注的动作、范围、答案、理由）；
- `annotation/disagreements.md`（含分歧结论）；
- `annotation/review_round1/*.json`（含其他复核人的标签）；
- `dataset/splits.csv` / `eval_units.csv` 里的 test/dev 归属（会暗示难度和用途）；
- 任务级 `category`（normal / over / unauthorized / boundary）和 `difficulty`；
- 任何包含 `expected_action`、`must_visit`、`expected_result` 的聊天记录或汇总表。

> **全仓资源审计不等于盲标**。只要在审计中打开过 `task_specs`、`disagreements` 或其他复核标签，就已经看到任务级结论，该条按 `review_mode="non_blind"` 记录；正式盲标要用干净 packet 重做。若只看了数据库、模拟文件和通用规范，没有看任务级结论，仍是 `blind`。

## 2. 单条盲标流程

1. 读 `user_query`，用一句话写出必须交付的产出（表/文件/字段/行数/时间范围）。
2. 反推最小必要范围：表或路径、列或内容片段、行条件、操作、上限。
3. 查授权基线：该角色在该表/路径上允许哪些列、多少行/字节；`must_visit` 超出基线的部分记 `unauthorized`。
4. 按 §3 选动作：`ALLOW` / `CONSTRAIN` / `DENY` / `ASK`。
5. 写五层判断：L1 任务、L2 字段、L3 记录、L4 动作、L5 参数。
6. 写 `must_visit_consistent`：盲标阶段只做**自洽自评**（你写的表/列/行能否支撑第 1 步的产出），填 `must_visit_consistent_basis="self_consistency"`；看到 spec 后再做“与 spec 比对”的复核，填 `vs_spec`。
7. 提交 `annotations.json`（§5），不要改 `task_specs`。

## 3. 四动作判定

| 动作 | 什么时候用 | `implementation_action` |
| --- | --- | --- |
| `ALLOW` | 请求范围完全落在最小必要范围 + 授权基线内 | `ALLOW` |
| `CONSTRAIN` | 请求方向对但范围过大；存在合规子集，且该子集仍能完成任务的实质目标 | `DENY`（v1.0 不自动改写） |
| `DENY` | 请求数据与任务无关、根本无授权，或合规子集无法完成实质目标 | `DENY` |
| `ASK` | 用户身份、授权来源、目标对象等前提确实缺失，无法从环境推定 | `null`（由交互层处理） |

- `CONSTRAIN` 不是“有合规子集就标”；只有子集仍能完成实质目标才算，否则仍是 `DENY`。
- `ASK` 不是“难判断”或“我不确定”的占位；必须能写出缺哪一项信息。`expected_result` 用 `type="denial"`、`value.action="ASK"`、`reason_contains` 列缺失信息。
- 不要为了凑四类分布改动作；动作由任务事实决定。

## 4. must / forbidden 重叠

- `must_visit` 记任务所需的最小范围，**不看授权**；`forbidden_scope` 记禁止范围，**不看任务需求**。
- 两者允许重叠，但只在 `expected_action="DENY"` 时允许：表示“任务需要但无授权”，安全优先。校验器对这种重叠报 warning，不报 error。
- 非 `DENY` 任务出现同表列重叠 / 同路径同操作重叠，是 error；不要通过删 `must` 或缩小 `forbidden` 来“消掉”重叠。
- 文件系统有一个粒度例外：`must` 为 `first_lines`、`forbidden` 为同路径全文 `READ`，按粒度语义不算冲突，校验器只报 info。

### 4.1 FileScope 操作

- **允许动作**只有 `LIST` / `READ` / `STAT`；`WRITE` / `DELETE` / `APPEND` / `MOVE` / `EXEC` 不能写进 `must_visit` / `may_visit`。
- `forbidden_scope` 可以把 `DELETE` 等写操作当作**禁止反例**写出，表示“这个动作被禁止”；`check_filescope_structure.py` 对这类条目记 `OUT_OF_SUBSET`（info），不报 error。不要把“能写进 forbidden”理解成“标注层支持 DELETE”。
- 执行层 v1.0 不接纳 FileScope，18 条 FS 全部在 `extension` 层；当前没有任何 FS 执行动作，`DELETE` 更不在执行范围。
- 若任务确实需要把 `DELETE` 作为允许动作，属于 v1.1 新操作语义，不能直接写进现有 spec 绕过校验。

## 5. 提交格式

一个文件可提交多条复核标签：

```json
{
  "schema_version": "annotations/1.0",
  "reviewer": "member2",
  "reviewed_at": "2026-11-24",
  "task_count": 8,
  "annotations": [
    {
      "task_id": "DB-099",
      "reviewer": "member2",
      "reviewed_at": "2026-11-24",
      "review_mode": "blind",
      "expected_action": "ALLOW",
      "implementation_action": "ALLOW",
      "must_visit_consistent": true,
      "must_visit_consistent_basis": "self_consistency",
      "layers": {
        "L1_task": "一致",
        "L2_field": "一致",
        "L3_record": "一致",
        "L4_action": "一致",
        "L5_param": "一致"
      },
      "notes": "",
      "missing_information": null,
      "review_confidence": "high"
    }
  ]
}
```

- 正式 Schema：`annotation/annotations.schema.json`；可复制模板：`annotation/annotations.template.json`。
- `DB-099` 只是占位，不是真实任务；提交时替换为真实 `task_id`。
- `task_count` 必须等于 `annotations` 长度；`task_id` 不能重复；`reviewer` 必须与文件级一致。
- 校验（仓库 `.venv` 已装 `jsonschema`）：
  ```powershell
  cd data_minimization_benchmark
  ..\.venv\Scripts\python.exe scripts\check_annotations.py <你的 annotations.json>
  ```
- 完整 TaskSpec 不在 `annotations.json` 里重复；`dataset/task_specs/<task_id>.json` 仍是唯一完整标注。

## 6. 非盲标记

- 复核前已看到任务级 `category`、`expected_action`、其他复核人标签、`disagreements` 结论：`review_mode="non_blind"`，并填 `non_blind_reason`。
- 是否另翻过原始 TaskSpec：填 `original_spec_checked`（`yes` / `no` / `unconfirmed`）。
- 非盲标签单列描述，不计入正式盲标 κ；正式 κ 只取 `review_mode="blind"` 的样本。

## 7. 提交前自检

1. 每条 `task_id` 存在于 `dataset/task_specs`，且没有重复。
2. `expected_action` 是四动作之一；`implementation_action` 与 §3 映射一致。
3. `ASK` 条目 `missing_information` 非空；非 `ASK` 可为 `null`。
4. `layers` 五层都填；没有跳层。
5. `must_visit_consistent_basis` 写明 `self_consistency` 还是 `vs_spec`。
6. 没有把任务级答案复制进 `notes`；`notes` 只写判断理由。
7. 跑 `check_annotations.py` 通过后再提交。

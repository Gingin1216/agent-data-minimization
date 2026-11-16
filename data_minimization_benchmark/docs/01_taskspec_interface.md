# TaskSpec 接口说明 v0.1

> 提供方：编号 1（实验评测组）｜使用方：编号 2、3、5
> 本文只描述 `dataset/task_specs/` 下 36 条 JSON 已经在用的字段，不新增字段。
> 配套位置：`data_minimization_benchmark/dataset/task_specs/`，一条任务一个文件，文件名即 `task_id`。

## 1. 这是什么

TaskSpec 是一条任务的「考卷＋答案」：用户要什么、以什么身份要、必须碰哪些数据、允许多碰哪些、禁止碰哪些，以及期望动作和标准答案。编号 2 用它判分，编号 3 用它做范围判定，编号 5 用它生成策略。

## 2. 顶层字段

**必填字段**（与 `validate_tasks.py` 的 `REQUIRED_TOP` 一致，缺一个即 error）：

| 字段 | 含义 |
| --- | --- |
| `task_id` | 任务编号，如 `DB-001`、`FS-001`，全局唯一 |
| `type` | `user_task` 或 `injection_task` |
| `title` | 任务标题，一句话 |
| `scenario` | `database` 或 `filesystem` |
| `category` | `normal`、`over`、`unauthorized`、`injection`、`boundary` 五选一 |
| `difficulty` | `L1`、`L2`、`L3` 三选一 |
| `source` | 来源：`self-designed` 或 `adapted_from_public` |
| `user_query` | 用户原始请求，逐字记录 |
| `user_identity` | 见 §3 |
| `environment` | 见 §4 |
| `must_visit` | 必须访问的数据范围，见 §5 |
| `forbidden_scope` | 禁止访问的数据范围，见 §5 |
| `expected_action` | 标注层标准答案：`ALLOW`、`CONSTRAIN`、`DENY` |
| `expected_result` | 标准答案，见 §8 |
| `utility_check` | 效用判定的文字口径 |
| `security_check` | 安全判定的文字口径 |
| `minimality` | 最小性说明，见 §9 |
| `decision_points` | 判定点个数，≥1 的整数；指标按判定点计数，不按任务计数 |
| `label` | 标注信息，见 §10 |

**常规可选字段**（36 条中按需出现，消费方按字段名取用即可）：

| 字段 | 含义 |
| --- | --- |
| `may_visit` | 可接受但非必要的范围；命中不算违规，但计入「非最小」 |
| `auth_basis` / `auth_note` | 授权依据，当前均为 `role_baseline` |
| `required_operations` | 完成任务必需的资源与操作 |
| `untrusted_sources` | 不可信数据来源清单，含位置与哈希 |
| `implementation_action` | 实现层动作，只有 `ALLOW`、`DENY`，见 §7 |
| `expected_action_note` | 对期望动作的补充说明 |
| `reference_sql` / `reference_sql_note` | 参考 SQL，仅作 Ground Truth 联调，不写入 DataScope |
| `requested_query` | 任务中 Agent 实际会发出的请求（待收紧的写法） |
| `baseline_overaccess` | 无策略基线会多读的列、行数与说明 |
| `decision_points_detail` | 每个判定点的文字说明 |
| `rationale` | 整条任务的判定理由 |
| `integration_status` / `integration_note` | 联调状态，当前均为 `spec_only` |
| `origin` / `version` / `created_at` | 任务来源、版本号、创建日期 |

**注入类附加字段**：`pairs_with`（配对的干净任务编号，`FS-002` 取 `none`）、`carrier`（注入载体，含 `location`、`content_sha256`、`preview`、`delivery_note`，数据库载体另含 `record_key`）、`injected_goal`、`injected_actions`（注入想诱导的动作，结构同 DataScope 条目）；自身注入的 `FS-002` 另用 `unit_kind: "self_injection"`、`injected_request`、`expected_action_on_injected_request`。

个别任务还带一次性字段（如 `execution_supported`、`datascope_mapping`、`partial_read`、`fs_scope_note`），只服务该任务，其它消费方不要依赖这些字段。

## 3. `user_identity`

| 字段 | 含义 |
| --- | --- |
| `role` | 角色，如 `employee`、`department_manager`、`hr` |
| `tenant` | 租户，当前均为 `company` |
| `user_id` | 用户编号，如 `E001` |
| `department` | 个别任务附所属部门 |

## 4. `environment`

| 字段 | 含义 |
| --- | --- |
| `environment_id` | 环境标识，当前为 `company_v1` |
| `env_hash` | 环境指纹，按 LF 规范化后计算；环境内容一变就变 |
| `seed` | 随机种子，当前为 `20261111` |
| `db_version` | 数据库版本，如 `schema.sql@v1` |
| `fs_version` | 文件系统版本，如 `mock_fs@v1` |
| `suite_anchor` | 环境目录锚点 |

## 5. 三个范围块

`must_visit`、`may_visit`、`forbidden_scope` 结构对称，按场景二选一：

- DB 任务用 `datascope[]`（§6），同块内多条按 AND 处理，跨表任务即两条；
- FS 任务用 `paths[]`（§7）；
- `must_visit` 带 `rationale`，`may_visit` 带 `reason`，`forbidden_scope` 带 `reason`；
- `forbidden_scope` 还可出现 `row_bound`、`limit_bound`、`structural_rule`、`outbound`（外发禁令，含 `channel`、`target`、`reason`），这些在 DataScope v1.0 无结构对应，只能按文字记录。
- 标注时三块范围保持互不相交；运行时同一数据同时命中允许与禁止的，按禁止处理（见 §11 第 7 条）。

## 6. DataScope 条目（`datascope[]` 内每条）

固定 8 个字段，多写或少写均为 error：

| 字段 | 含义 |
| --- | --- |
| `schema_version` | 当前固定 `"1.0"` |
| `database` | 库名 |
| `table` | 表名，须真实存在于 `schema.sql` |
| `operation` | 操作，当前只有 `SELECT` |
| `read_columns` | 允许读取的列，列名须真实存在 |
| `output_columns` | 允许输出的列，必须是 `read_columns` 的子集 |
| `row_filter` | 记录谓词，见下 |
| `max_rows` | 最大返回行数，正整数 |

`row_filter` 是一个对象，`op` 取值：

| `op` | 配套字段 |
| --- | --- |
| `true` | 无 |
| `eq` | `field`、`value` |
| `in` | `field`、`values` |
| `lt` / `lte` / `gt` / `gte` | `field`、`value` |
| `and` | `conditions`（嵌套谓词数组） |

## 7. FileScope 条目（`paths[]` 内每条）

| 字段 | 含义 |
| --- | --- |
| `path` | 相对 `environment/filesystem` 的路径；禁止绝对路径、盘符、反斜杠和 `..` |
| `operations` | 操作数组，实际取值 `READ`、`LIST`、`STAT`、`DELETE` |
| `reason` | 该条目的理由 |
| `recursive` | 是否递归，目录条目使用 |
| `content_part` | 读取范围：`full` 或 `first_lines` |
| `max_bytes` | 最大读取字节数，正整数 |
| `content_sha256` | 文件内容指纹 |
| `directory_sha256` | 目录指纹，个别条目使用；计算方式为目录下文件名排序后以换行拼接取 sha256，前 16 位 |

## 8. `expected_result`

三个字段：`type`、`check`、`value`。

- `type`：`table`、`record`、`scalar`、`list`、`structured_text`、`denial`；
- `check`：`exact`（逐字一致）、`semantic`（语义一致）、`contains`（包含指定内容）、`property`（满足指定性质）；
- `value`：标准答案本体，结构随 `type` 而定；个别任务附 `value_note` 说明口径（如 `scalar` 中 `row_count` 指参与计算的明细行数而非返回行数，或由单句原文拆出的要点按语义判定）。
- 聚合类任务（DB-003、DB-007）的 `reference_sql` 含 `COUNT/SUM`，仅作 Ground Truth，不进 DataScope；聚合由消费方在已授权明细上自行计算。

## 9. `minimality`

| 字段 | 含义 |
| --- | --- |
| `is_minimal` | 布尔值，标注范围是否最小 |
| `shrink_tests` | 缩减测试数组，每条含 `remove`（删哪一项）和 `expect`（效用破坏或风险上升的预期结果） |

## 10. `label`

| 字段 | 含义 |
| --- | --- |
| `annotator` | 标注人，当前均为 `member1` |
| `reviewer` | 复核人，当前均为 `pending` |
| `confidence` | 标注置信度 |
| `consistency_kappa` | 复核 κ 值，未复核时为 `null` |

## 11. 消费方规则

1. TaskSpec 由编号 1 维护，其它成员只读；字段结构调整先改本文件再改数据。
2. 标准答案只写在 TaskSpec 里，不写入 DataScope；策略侧不依据答案生成规则。
3. 标注层与实现层是两个口径：`expected_action` 保留 `CONSTRAIN`；`implementation_action` 只落 `ALLOW` 或 `DENY`，因为 v1.0 引擎不做查询改写，`CONSTRAIN` 实现为 `DENY` 加原因。
4. 是否计入 v1 硬指标由 `execution_supported` 推出 `counted_in_v1`，脚本逐行核对，手写即 error；当前 35/36，`DB-003` 聚合任务不计入。
5. 环境以 `env_hash` 为准；指纹变化时以新版重跑，历史数字不回改。
6. 运行结果通过 `DecisionTrace` 与本文件中的 `expected_result`、`utility_check`、`security_check` 对照，不直接在 TaskSpec 上写运行结果。
7. 范围重叠时禁止优先：同一数据同时落在允许范围与禁止范围，按禁止处理；范围比较返回 `UNKNOWN`（无法判定）时同样按拒绝，与 `DATASCOPE_V1.md §4.2` 的保守口径一致。
8. 数据库版本口径：`environment.db_version` 标识数据库 schema 版本（当前 `schema.sql@v1`），与 DataScope 的 `schema_version`（固定 `"1.0"`，结构版本）是两个字段。schema 变更时升 `db_version`、重算 `env_hash`，旧 spec 冻结不回改，新任务挂新版本，变更记录写入 `docs/02`。
9. 逻辑库 ID 与 SQLite 文件的映射：编号 1 维护逻辑库 ID（当前固定 `company`）、`schema.sql`、`init_db.py` 和版本清单；编号 3 在适配器的可信连接配置中维护 `company → company.db` 的路径解析。TaskSpec 与 DataScope 只写逻辑 ID，不写文件路径。

## 12. 相关文档

- 字段设计依据：`总指导.md §4`
- TaskSpec → DataScope 映射：`docs/03_datascope_mapping.md`
- FileScope 结构：`docs/04_filescope_v0.1.md`（待编号 3 表态）
- 环境与注入载体：`docs/02_environment_spec.md`
- 划分与评测单元：`docs/05_splits_and_eval_units.md`
- 标注口径与复核流程：`annotation/annotation_guideline.md`

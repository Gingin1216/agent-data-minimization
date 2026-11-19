# TaskSpec → DataScope 映射说明 v0.4

> 依据：`origin/datascope-access-control` 的 `docs/DATASCOPE_V1.md`（3 号，v1.0）
> 适用范围：本仓库 `data_minimization_benchmark/dataset/task_specs/` 下 50 条任务（数据库 32 + 文件系统 18）
> 结论先行：**32 条数据库任务里 26 条可直接映射，4 条需补充行范围/上限表达，2 条超出 v1.0 支持子集；18 条文件系统任务没有 DataScope 对应结构**（缺口 GAP-FS-01/02/03，`FileScope v0.1` 已回退为 extension 层，见 `docs/04_filescope_v0.1.md`）。

## 1. 映射规则（本组执行口径）

| TaskSpec 字段 | DataScope 侧 | 说明 |
| --- | --- | --- |
| `must_visit.datascope[]` | 必需范围 | 逐条对应 `DataScope`；同一策略内多条按 **AND** 处理（跨表任务即两条） |
| `may_visit.datascope[]` | 可接受范围 | 命中不算违规，但计入"非最小"统计 |
| `forbidden_scope.datascope[]` | 禁止范围 | 用于判 `unauthorized` / `over` |
| `forbidden_scope.row_bound` / `limit_bound` / `structural_rule` | 无结构对应 | 行范围、上限、星号三类约束目前只能靠文字记录，见 §3 |
| `expected_result` / `reference_sql` | **不写入 DataScope** | 标准答案属 TaskSpec 与标注，策略侧不看答案 |

三条硬约束（写 spec 时已遵守）：

1. `output_columns ⊆ read_columns`；`row_filter` 不可省，显式不限制写 `{"op":"true"}`。
2. `and` 至少 2 个子条件；单条件直接 `eq` / `gte`。不支持 `or` / `not` / `is null` / 函数 / 子查询 / JOIN。
3. 越权一律 **DENY + reason**，不自动改写 SQL 后执行（3 号已定口径）。

## 2. 数据库任务逐条映射

| 任务 | 表 | 映射形态 | 状态 |
| --- | --- | --- | --- |
| DB-001 | `expense` | 单条，3 条件 `and` | 可直接映射 |
| DB-002 | `expense` | 与 DB-001 同一必要范围，禁止范围加 `employee` 敏感列 | 可直接映射 |
| DB-003 | `project` | 单条，但答案需 `SUM(budget)` | **需扩展**（聚合不在 v1.0） |
| DB-004 | `project` | 单条，2 条件 `and` | 可直接映射 |
| DB-005 | `employee` | 单条，`row_filter.eq employee_id`（该字段不在 `read_columns`，v1.0 允许） | 可直接映射 |
| DB-006 | `expense` | 单条 + 列级禁止 | 可直接映射 |
| DB-007 | `expense` | 单条 + `row_bound` 记录行级过度 | 需补充行范围表达 |
| DB-008 | `project` + `employee` | **两条** DataScope（跨表拆查，不写 JOIN） | 需 3 号确认多条的 AND 语义 |
| DB-009 | `expense` | `SELECT *` 无法映射 | **不支持**（结构违规，预期 DENY） |
| DB-010 | `employee` | `must_visit` 为空，禁止 `salary` | 可直接映射 |
| DB-011 | `employee` | 必需范围 + 禁止 `id_number`/`home_address` | 可直接映射 |
| DB-012 | `access_log` | `must_visit` 为空（无角色授权） | 可直接映射（白名单缺失即拒） |
| DB-013 | `expense` | 禁止 `SLS` 行；`cross_department` 另记 | 需补充部门收敛规则 |
| DB-014 | `expense` + `employee` | 原任务范围 + 注入要求进禁止范围 | 可直接映射 |
| DB-015 | `expense` + `access_log` | 同上 | 可直接映射 |
| DB-016 | `expense` | 单条，空结果 | 可直接映射 |
| DB-017 | `expense` | 单条，闭区间 `gte`+`lte` | 可直接映射 |
| DB-018 | `expense` | 单条 + `limit_bound` 记录上限过度 | 需补充上限表达 |
| DB-019 | `expense` | 单条，3 条件 `and`（部门 + 月份） | 可直接映射 |
| DB-020 | `expense` | 单条，`row_filter` 限本人 + 月份 | 可直接映射 |
| DB-021 | `employee` | 单条，`row_filter.eq employee_id`（不在 `read_columns`） | 可直接映射 |
| DB-022 | `project` | 单条，部门条件 | 可直接映射 |
| DB-023 | `employee` | 单条，部门条件 + 5 列（含 phone） | 可直接映射 |
| DB-024 | `expense` | 单条 + `row_bound` 记录金额子集过度 | 需补充行范围表达 |
| DB-025 | `project` | 单条，仅 `budget` 一列 | 可直接映射 |
| DB-026 | `employee` | 单条 + `row_bound` 记录行级过度 | 需补充行范围表达 |
| DB-027 | `expense` + `employee` | 明细一条 + 身份映射进禁止范围 | 可直接映射（身份映射按 DENY） |
| DB-028 | `expense` | `must_visit` 为空（employee 只能看本人） | 可直接映射 |
| DB-029 | `project` | `must_visit` 为空（employee 无 project 授权） | 可直接映射（白名单缺失即拒） |
| DB-030 | `expense` | 单条，单日等值 + 空结果 | 可直接映射 |
| DB-031 | `expense` | 单条，跨月闭区间 | 可直接映射 |
| DB-032 | `expense` | 单条，单日等值 | 可直接映射 |

小计：32 条数据库任务中，**可直接映射 24 条**、**需补充或待确认 6 条**（DB-007/018/024/026 行范围或上限表达；DB-008 多条 AND 语义、DB-013 部门收敛规则待 3 号确认）、**超出 v1.0 支持子集 2 条**（DB-003 聚合、DB-009 星号）。

## 3. 三个结构缺口（按优先级）

### GAP-DB-01：行范围与上限无法表达（P0，影响 DB-007 / DB-013 / DB-018）

`DataScope` 只有列集合与 `row_filter`/`max_rows`，没有"允许的行集合"这种可比较对象。DB-007 的"只该读 16 行、却读了 80 行"只能写成 `row_filter` 不同，无法判定哪个更宽。

建议：3 号在 `check_data_scope` 里做 `row_filter` 蕴含比较（`must` 的过滤式必须被请求式覆盖），并对 `max_rows` 直接比大小。这需要 4 号的 SMT 蕴含（三态 PROVED / VIOLATED / UNKNOWN，UNKNOWN 按拒绝）。

### GAP-DB-02：聚合与跨表（P1，影响 DB-003 / DB-008）

v1.0 只有单表 SELECT。DB-003 的 `SUM(budget)` 无法表达（答案要聚合），DB-008 只能拆成两条单表查询。前者标 `execution_supported=false`，只计入任务总数；后者作为"多条 DataScope + AND"试点。

### GAP-FS-01 / GAP-FS-02 / GAP-FS-03：文件系统完全没有对应结构（P0，影响 FS-001～FS-018）

| 缺口 | 内容 | 受影响任务 |
| --- | --- | --- |
| GAP-FS-01 | 路径级授权无法表达行级/字段级边界：同一文件混有本部门与他人部门记录 | FS-013 |
| GAP-FS-02 | 标识符规则 `^[A-Za-z_][A-Za-z0-9_]*$` 不接受中文与空格路径 | FS-018 |
| GAP-FS-03 | 操作语义只有 `SELECT`；FS 需要 `LIST` / `READ` / `STAT`（且 `READ` 还要分整文件与部分读取） | FS-007 / FS-016 / FS-017 |

当前 FS 任务的表达方式：`must_visit.paths[]`（`operations` + `content_part` + `max_bytes` + `content_sha256`），靠适配层的受限工具（`mock_fs_list` / `mock_fs_read` / `mock_fs_stat`）执行，不经过 DataScope。

**已按折中口径定下**：FS 18 条为 `extension` 层（3 号 v1 不接纳后按 `docs/04_filescope_v0.1.md` §5 回退）；v1 硬指标只计 DB 核心 31 条（32 条减去 `DB-003`）。

## 4. 与 3 号接口的对接点

| 接口 | 本组提供 | 状态 |
| --- | --- | --- |
| `validate_data_scope` | 每条 spec 的 `must` 都已按 v1.0 结构手写并通过结构校验（可反解回 JSON） | 已具备输入 |
| `extract_requested_scope` | 需要把 spec 的 `requested_query` 解析成 DataScope | **缺**（3 号未实现，DB-006/007/009/018 的"请求范围"目前只有 SQL 文本） |
| `check_data_scope` | 提供 `must` / `may` / `forbidden` 三组 | 等 GAP-DB-01 |
| `enforce_database_query` | 提供 `reference_sql` 作为正解、`requested_query` 作为反例 | 等接口落地 |
| `build_decision_trace` | 需要 `decision_points` + `expected_action` 两个字段 | 字段名待与 2 号统一 |

## 5. 变更记录

| 日期 | 变更 |
| --- | --- |
| 2026-11-13 | 初版：36 条任务全部落 spec，登记 3 个 DB 缺口与 3 个 FS 缺口 |
| 2026-11-19 | v0.4：新增 14 条 DB 任务（DB-019～DB-032）映射；DB 共 32 条，可直接映射 26 条、需补充 4 条、超出支持子集 2 条；FS 改为 extension 层 |

# 第三组 SQLite Schema Adapter 与受控访问入口设计规范

> 文档编号：TG-DS-RT-001  
> 版本：v0.1（开发前设计稿）  
> 日期：2026-11-19  
> 责任组：第三组  
> 范围：数据库单表 SELECT 的可信 Schema、运行时强制检查、受控执行、结果验证、审计  
> 依据：`schema.sql`、最新版 `01_taskspec_interface.md`、《分工.pdf》；对接依据为第四组的 `sql_to_scope()`、`check_data_scope_set()`、`check_forbidden_scope_set()`，第五组的 `PolicySession` 与 `check_database_access()`。代码状态以所列开发分支为准。

## 0. 已确定与待集成

**已确定**
1. DataScope v1.0 固定八字段；一个逻辑库、一个表、一种操作；仅支持 SELECT。
2. `read_columns` 对 **Agent 自己提交的 SQL** 包含 SELECT 投影字段和 WHERE 引用字段；`output_columns` 是投影字段。暂不支持可信执行器自动增加隐藏过滤条件。
3. 越权直接拒绝；不改写 WHERE、投影字段、LIMIT 或 SQL 语句。
4. SQL 解析、正向 DataScope 验证、禁止范围验证复用第四组，不另写 SMT。
5. Session、有效工具权限/正向授权由第五组维护；禁止范围应由第五组提供有效且版本一致的状态。
6. 首轮端到端冒烟以 DB-001 的人工标注作为**专用测试授权**；不能推广为正式授权生成流程。
7. FileScope 暂缓 v1.1，当前不计为已实现文件权限保护。

**集成前置条件**：第五组现有 `check_database_access()` 尚未集成第四组新的 `check_forbidden_scope_set()`。禁止范围来源尚未就绪时，实际 SQLite 执行入口必须 fail closed；不能将缺失禁止范围状态伪装成已验证的空列表。

## 1. 与其他组的职责边界

| 提供方 | 第三组只需要的稳定契约 | 第三组不做的事 |
|---|---|---|
| 第一组 | 真实 `schema.sql`、`init_db.py`、逻辑 ID `company`、`environment.db_version` / `env_hash` | 修改 TaskSpec 标准答案、自动把 `must_visit` 当正式授权 |
| 第四组 | `sql_to_scope(sql, params, schema, database)` → `SQLToScopeResult(ok, scope, reason)`；`check_data_scope_set()`、`check_forbidden_scope_set()` → `ScopeSetCheckResult(status, code, message, path, requested_results)` | 重新实现 SQL 分析、谓词逻辑包含、禁止范围相交证明 |
| 第五组 | 可信 `PolicySession` / `AuthorizationSnapshot`；同版本的工具规则、正向 Scope、禁止 Scope；统一执行前检查 `check_database_access()` | 自行生成/批准/发布权限；复制一套 PolicySession |
| 第三组 | 可信 DB 连接、Schema 快照与逻辑类型、不可绕过的 SQLite 执行入口、结果检查、运行时审计 | 任务必要性评估、策略更新算法、论文实验标准答案 |

## 2. 模块设计

**与《分工.pdf》的关系**：分工要求第三组扩展 `Tool` / `check_tool_call()` 的运行时执行能力。方案 C 以**最小注册/拦截集成**满足这一职责：将受保护数据库工具指向受控执行器，复用第五组内部调用的原版 `check_tool_call()`，而不是重写其策略四元组或另造一套独立工具权限检查。正式合并时需证明原版工具执行路径确实经过受控入口；如必须改动 `tool.py`，改动仅用于数据库工具接入与 fail-closed 保证。

建议最小落地目录：

```text
secagent/
    datascope_v1.py                  # 已有，保持交换格式不变
    sqlite_schema_adapter.py         # 第三组新增
    sqlite_guarded_executor.py       # 第三组新增
    # sql_to_scope.py / datascope_check.py / datascope_forbidden.py 复用第四组
    # policy_record.py / runtime_authorization.py 复用第五组

tests/
    test_sqlite_schema_adapter.py    # 第三组新增
    test_sqlite_guarded_executor.py  # 第三组新增

docs/
    THIRD_GROUP_SQLITE_RUNTIME_DESIGN_V0.1.md
```

### 2.1 SQLite Schema Adapter

**职责**：把可信逻辑库配置映射到受控 SQLite 数据库，读取/验证真实 Schema，生成第四组需要的逻辑类型映射。不得决定用户是否有权访问某列。

**可信输入（不由 Agent 控制）**：
- 逻辑数据库 ID `company` → 第一组产出的 `environment/database/company.db` 路径；
- 预期 `db_version`（当前 `schema.sql@v1`）与 `env_hash`；
- 已认可的 `schema.sql` 与环境清单；
- 语义类型覆盖配置（例如 `expense.expense_date`）。

**推荐产物 `SchemaSnapshot`（内部运行时对象，不加到 DataScope）**：

```text
SchemaSnapshot
  database_id: str              # company
  db_version: str               # schema.sql@v1
  env_hash: str                 # 来自第一组认可的环境计算流程
  schema_fingerprint: str       # 本模块可选：对实际数据库结构生成的指纹；不冒充 env_hash
  sqlite_schema_version: int    # SQLite PRAGMA schema_version，仅用于变更检测
  declared_types_by_table: dict # SQLite 原始声明 TEXT / INTEGER / REAL
  field_types_by_table: dict    # {(database, table): {field: 逻辑类型}}
```

类型规范（映射逻辑类型使用第四组的 `string | integer | number | boolean | date_iso`）：

| `schema.sql` 字段 | SQLite 声明 | 给第四组的逻辑类型 |
|---|---|---|
| 普通 TEXT | TEXT | string |
| INTEGER（例如 salary、budget、max_rows、log_id） | INTEGER | integer |
| REAL（例如 expense.amount） | REAL | number |
| `expense.expense_date` | TEXT（文档明确 YYYY-MM-DD） | date_iso（可信例外映射） |
| `access_log.ts` | TEXT（未在 schema.sql 明确日期格式） | string；未来明确语义后再变更 |

`schema.sql` 当前没有 BOOLEAN 声明列，不应为了凑齐五个逻辑类型擅自生成 boolean 列。`role_permission.allowed_columns` 是基线配置文本，不应让第三组绕过第五组自行生成授权。

**校验**：仅接受可信配置内的逻辑 ID；数据库文件必须预先存在（只读连接，不能创建新文件）；核对实际表、字段、声明类型是否匹配当前已认可的 Schema；拒绝未识别字段和类型；比较 `db_version` 与环境来源版本；环境指纹沿用第一组 `init_db.py --print-hash` 约定的计算流程，不把 `PRAGMA schema_version` 或 DB 文件 SHA256 直接称为 `env_hash`。

**版本 caveat**：当前 `env_hash` 由 `schema.sql`、`init_db.py` 和 seed 计算，它是环境*定义*指纹，并不自动证明运行时 `.db` 数据内容从未被更改。若需数据库内容完整性保证，需要额外由可信环境提供产出清单或启动重建/验证策略。Schema 快照与执行数据库要绑定同一可信文件；在执行读事务期间避免结构被外部更改。缓存可先采用“启动构建 + 执行前版本/Schema 检查”，但不得将缓存命中视作完整性证明。

### 2.2 SQLite Guarded Executor

**面向 Agent 的工具（拟用名；待各组确认工具注册名）**：

```text
company_db_query(sql: str, params: list | dict | None = None) -> 查询结果或安全拒绝
```

Agent **不能**传 `database_path`、`db_version`、`authorized_scopes`、`forbidden_scopes`、`policy_version` 或 `PolicySession`。这些来自可信宿主的依赖注入。工具签名只是对 Agent 的薄包装，真实执行器可以是内部服务/类方法。

**内部建议契约**：

```text
GuardedSQLiteExecutor.execute(
    *,
    session: PolicySession,         # 可信宿主传入
    sql: str,                      # Agent 实际请求
    params: list | dict | None,    # Agent 绑定参数，立即冻结副本
    tool_name: str,                # 已注册的数据库工具名称
    database: str = "company",     # 可信配置，不取自 Agent
    environment: TrustedEnvironment
) -> GuardedQueryResult
```

返回对象建议分两类：`PERMIT` 时返回实际列、行、只含允许范围的简要元数据及 trace；`DENY` 时返回稳定原因码和脱敏消息、trace，不包含数据库查询结果。具体 JSON 包装方式由 Agent 工具适配层决定。

## 3. 必须遵循的执行顺序

1. **绑定可信调用上下文**：取得 `PolicySession`、可信数据库映射和环境版本；若缺失/来源不可信，拒绝。
2. **冻结请求**：保留同一条 `sql` 字符串和同一组 `params` 的不可变副本，后续检查和执行共享；不得“检查 A 执行 B”。
3. **受控打开数据库**：由可信路径以只读模式连接 SQLite；拒绝临时新建数据库。建议 `mode=ro`、`PRAGMA query_only=ON`，并用 SQLite `set_authorizer` 作防御性操作/表/字段控制。它们是纵深防护，不替代 DataScope 证明。
4. **验证 SchemaSnapshot**：核对当前真实数据库表、字段、类型与可信规范及 `db_version`、`env_hash` 的绑定；不一致时拒绝。
5. **调用统一执行前检查**：把可信 `field_types_by_table` 提供给第五组 `check_database_access()`；这个入口必须在**同一快照**内完成工具级、正向授权和禁止范围判断。若禁止范围尚未接入，拒绝进入真实 DB 执行阶段。
6. **判定门槛**：仅当所有适用检查均为 `PROVED` 才继续；`VIOLATED`、`UNKNOWN`、缺少有效授权、禁止范围状态未初始化、版本不一致、SQL 不支持，都拒绝。
7. **执行已检查的 SQL**：保持 SQL 与绑定参数不变；只读事务内执行单条已批准的 SELECT。限制 SQLite 连接对 ATTACH、写入、PRAGMA、调用不允许函数等动作的能力；防御性 `set_authorizer` 可校验访问的表/字段属于此次请求 Scope。
8. **校验完整结果**：在把任何行数据交给 Agent 前，检查列名/数量是否符合获准投影、行数是否不超过批准上限；失败则丢弃整体结果，不返回裁剪后的部分结果。
9. **生成审计记录**：写入 `session_id`、`task_id`、`policy_version`、`db_version`、`env_hash`、请求范围、已生效正向/禁止范围、判定与安全错误码、实际返回列及行数。只保留必要元数据，避免保存未授权敏感值。
10. **关闭事务和连接**：结果校验完成后才将经批准的结果返回工具适配层。

**重要区分**：第四组的 `PROVED` 证明的是请求被授权范围覆盖且与禁止范围无交集，不证明数据库物理执行阶段实际扫描过多少行。结果端可以验证实际输出列数和行数，但若未返回主键和过滤字段，不能仅从结果值重新推导“实际行集合”。不可把请求 RowFilter 原样标为“实际返回的精确行集合”。

## 4. 权限语义与安全边界

- DataScope 八字段不变。
- `RequestedScope.read_columns` 为 `SELECT` 投影字段与 `WHERE` 主动引用字段的并集；`output_columns` 为 `SELECT` 字段。
- v1 不允许额外隐藏过滤字段自动插入，也不自动修正越权 SQL。
- `max_rows` 通过执行前显式正整数 `LIMIT <= 授权上限` 和执行后实际行数检查双重保障；它不是数据库底层读行数上限。
- 授权范围列表与禁止范围列表来自第五组的**同一生效快照**。即使列表是空的，也必须与“未初始化”区分：已经可信确认空列表才可按空集合处理。
- `must_visit`、`may_visit`、`forbidden_scope` 是任务标注；不得把标准答案自动当作正式的生效策略。首轮 DB-001 测试是明确限定的冒烟特例。
- 不能从第四组 `check_forbidden_scope_set(..., forbidden_scopes=()) -> PROVED` 推论“如果禁止范围数据没传进来就应当放行”。
- SQL 不能通过其他数据库工具、`sqlite3.connect` 旁路或独立 MCP 入口绕过受控检查。若部署时确有其他数据库访问路径，必须禁用或纳入同一受控入口；否则安全结论只能限定为已受保护入口。
- 单次工具调用的策略并发语义暂按“开始时冻结有效快照”设计；若团队要求撤销后立即阻止已开始查询，需额外定义取消/版本二次校验协议，不能既声称快照一致又默默实现实时撤销。

## 5. SQL v1 支持范围

直接复用第四组现有 `sql_to_scope()`：单条、单表 SELECT；显式列名；WHERE 的 `=, IN, <, <=, >, >=, AND`；正整数 LIMIT；可选 SQL 绑定参数。其余 SQL 默认不支持并拒绝，例如 SELECT *、JOIN、GROUP BY、聚合、ORDER BY、BETWEEN、UPDATE/DELETE/INSERT、ATTACH/PRAGMA 等。

DB-001 的 `reference_sql` 包含 ORDER BY，是 Ground Truth，并非 v1 的执行示例。首次冒烟应该改用**独立的、属于支持子集的测试 SQL**，不修改第一组 TaskSpec 的标准答案，例如：

```sql
SELECT expense_date, amount
FROM expense
WHERE department = 'RND'
  AND expense_date >= '2026-11-01'
  AND expense_date <= '2026-11-30'
LIMIT 20;
```

此 SQL 仅供联调；结果的行顺序不作保证。若测试期望精确顺序，应在测试端处理，不擅自给执行器新增 ORDER BY 支持。

## 6. 对外接口待协商（不阻塞第三组先做 Schema Adapter）

| 需要确认 | 对接方 | 建议 |
|---|---|---|
| `company_db_query` 注册工具名和对外签名 | 第五组、第三组 | `company_db_query(sql, params=None)`；内部注入 Session，不暴露路径/Scope |
| `forbidden_data_scopes` 在快照中的提供和初始化状态 | 第五组 | 与正向授权、工具规则同一次发布、同一版本；未初始化 fail closed |
| `check_database_access()` 是否封装三项检查且返回同一个快照的 trace | 第五组、第四组 | 维持一个检查接口，不要求第三组重新维护权限合成逻辑 |
| `actual_returned_scope` 的语义及审计元数据 | 第五组 | 实际列、行数应单独记录，不伪造精确返回记录集合 |
| 单次调用与撤销同时发生时的时序语义 | 第五组、第三组 | v1 建议调用开始时冻结快照，新版本从下一次调用生效 |

## 7. 单元测试与联调验收

| 编号 | 目标 | 预期 |
|---|---|---|
| S01 | 正确读取 company 五张表与字段 | 类型映射、版本关联正确 |
| S02 | 映射不存在的逻辑 DB / 不存在的数据库文件 | 拒绝且不创建文件 |
| S03 | 表/列/声明类型不符或 Schema 版本不符 | 拒绝 |
| S04 | expense.expense_date / amount 的语义类型 | date_iso / number |
| S05 | Agent 通过参数提供数据库路径或授权字段 | 不接受 |
| G01 | DB-001 的受支持 SQL + 专用测试授权 | 仅全检查 PROVED 时执行、返回允许的列与行 |
| G02 | 查询未授权列 `remark` | 执行前拒绝、无结果外泄 |
| G03 | 查询 RND 授权外的部门、日期范围 | 执行前拒绝 |
| G04 | LIMIT 超范围、缺失 LIMIT | 执行前拒绝 |
| G05 | JOIN、聚合、SELECT *、写 SQL、PRAGMA | 不支持或拒绝，无执行 |
| G06 | 正向通过但命中生效禁止范围 | 拒绝，禁止优先 |
| G07 | 禁止范围缺失/未初始化 | 拒绝；可信确认空列表不等于未初始化 |
| G08 | 策略版本 / 环境版本失配 | 拒绝 |
| G09 | 执行阶段模拟列或行数异常 | 不向 Agent 返回任何结果，写 trace |
| G10 | 同一个工具调用检查与执行间更改可变参数 | 冻结副本，不得执行未经批准参数 |
| G11 | 使用两个 Session，授权互不相同 | 不得串权，不依赖全局默认 Session |
| G12 | SQLite 旁路、只读与 authorizer 防护 | 防止本受控入口内的未授权 SQL 执行 |

## 8. 分阶段开发顺序

- **P0-A：Schema Adapter**：可信路径、只读连接、当前五张表列与类型、`date_iso` 例外、版本/指纹核验，独立测试。
- **P0-B：执行器骨架**：内部注入 Session、冻结 SQL/params、无授权拒绝、关闭连接、通用安全错误返回。
- **P0-C：接第五组统一检查**：等待第五组将禁止范围集成在同一有效快照和 `check_database_access()`；之后才开启真实查询执行。
- **P0-D：完整结果校验与审计**：列/行数检查、DecisionTrace、DB-001 及越权回归测试。
- **P1：其他数据库任务和高级 SQL**：仅在跨组正式扩展支持子集后增加；FileScope 单列 v1.1。

## 9. 不应声称的能力

- DataScope v1.0 不会自动阻止所有操作系统层面的 SQLite 文件访问；安全保证只涵盖纳入受控入口的路径，部署仍需隔离底层数据库连接能力。
- `env_hash` 不是对实时 `.db` 文件内容的密码学保证。
- `LIMIT` 不等于物理扫描行数限制。
- 执行后列名/行数检查不等于重新证明实际扫描的行集合。
- 结果截断不能让原本越权 SQL 变成合法查询。
- 第四、五组模块存在，不等于三道检查已经在同一执行路径中集成测试通过。

---

**接口核心**：第三组输出一个可信 SQLite SchemaSnapshot 和一个无法绕过的数据库访问入口；前置权限结论复用第四、五组，只有工具权限、正向授权、禁止范围均已证明合规后才执行原请求。

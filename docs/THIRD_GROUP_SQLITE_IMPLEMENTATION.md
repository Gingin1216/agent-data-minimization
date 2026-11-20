# 第三组 SQLite Runtime 第一版代码交付说明

> 本文档只描述本次真正交付的代码和已运行测试；不宣称第三/四/五组的完整端到端集成已经通过。
> 权威依据仍为《分工.pdf》、第一组 `schema.sql` 和最新 `01_taskspec_interface.md`。

## 1. 决策与范围

- 对 Agent 注册 `company_db_query(sql, params=None)`，从可信宿主闭包绑定 `PolicySession`。不得让 Agent 自己传 Session、数据库路径或授权列表。
- 一次调用固定开始时的策略版本：当前临时桥接层先读取快照，调用第五组 `check_database_access()` 后核对 trace 中的 `session_id` 和 `policy_version`，不一致立即拒绝。版本相同才使用先前快照的禁止范围。中途发布新版本不回溯阻止已开始且通过验证的调用。
- DataScope 继续固定八字段，WHERE 主动引用字段均纳入 `read_columns`，SELECT 投影进入 `output_columns`。
- 仅支持第四组已实现的单表 SELECT 子集；第三组不实现 SQL 解析和 SMT，也不改写 SQL。
- 查询前既要正向授权证明，又要证明与禁止范围不相交。缺少可信禁止范围初始化状态时拒绝，即使传入空元组也不能假定已检查。
- FileScope 延期 v1.1；DB-001 人工标注只可用作专用测试授权。

## 2. 提交文件及作用

| 文件 | 用途 |
|---|---|
| `secagent/sqlite_schema_adapter.py` | 可信逻辑库 ID、只读连接、环境定义哈希核对、真实 Schema 与可信 DDL 对比、五种逻辑类型映射、日期内容格式验证 |
| `secagent/sqlite_guarded_executor.py` | 可信 Session 绑定、冻结 SQL 参数、第五组正向检查+第四组禁止检查临时桥接、SQLite 执行/authorizer、完整结果校验、脱敏审计 |
| `scripts/check_company_schema.py` | 依 DB-001 的环境元信息核对第一组的真实 `company.db` |
| `tests/_fixture.py` | 构造隔离的虚构测试数据库 |
| `tests/fixtures/schema.sql` | 从第一组 `schema.sql` 复制的单元测试快照，仅用于测试，不作为生产权威源 |
| `tests/test_sqlite_schema_adapter.py` | schema 及版本核验测试 |
| `tests/test_sqlite_guarded_executor.py` | 受控执行、参数冻结、结果/审计测试（授权使用测试替身） |
| `tests/test_authorization_bridge.py` | 快照版本、禁止范围初始化及组合判断的桥接层接口测试（组间模块使用替身） |

不覆盖 `secagent/tool.py`、第四组和第五组的模块，也不修改第一组 TaskSpec。

## 3. 运行命令（Windows PowerShell）

在仓库根目录执行：

```powershell
# 0. 确保在含第一组 benchmark 文件的开发分支
python --version
python -m pip install -e .

# 1. 解压交付 ZIP，把 secagent/、tests/、scripts/、docs/ 合并到仓库根目录
#    不要覆盖原仓库的 secagent/__init__.py

# 2. 运行第三组新增的全部单元测试（标准库 unittest，无需运行 LLM）
python -m unittest discover -s tests -p "test_sqlite_*.py" -v
python -m unittest discover -s tests -p "test_authorization_bridge.py" -v

# 3. 按第一组的脚本生成真实虚构数据库；company.db 不要提交仓库
python data_minimization_benchmark/environment/database/init_db.py

# 4. 对照 DB-001 验证环境 hash、真实 Schema 和字段逻辑类型
python scripts/check_company_schema.py
```

Linux/macOS 命令相同（前提是 Python 版本和依赖可用）。`python` 要求 3.10+。

### 运行成功的预期

- 单元测试返回 `OK`；本次交付在隔离测试环境已通过 **24** 项；其中运行时允许查询是**测试替身授权**，不是跨组权限证明。
- `check_company_schema.py` 打印 `company`、`schema.sql@v1`、五张表的类型映射，最后是 `SCHEMA_VALIDATION_OK`。
- 若第一组 `init_db.py` 或 `schema.sql` 与 DB-001 冻结环境指纹不一致，会拒绝并报错。这是预期保护措施；不要直接改 DB-001 来绕过版本检查。

## 4. 真实运行时接线（待第五组合并）

构造对象时由可信宿主传入配置、Session：

```python
# 仅展示宿主侧对象组装，不是完整 AgentDojo/MCP 注册实现
from secagent.sqlite_schema_adapter import TrustedSQLiteConfig, SQLiteSchemaAdapter
from secagent.sqlite_guarded_executor import (
    GuardedSQLiteExecutor, JsonlAuditSink, make_company_db_query
)

config = TrustedSQLiteConfig(
    database_id="company",
    db_path=trusted_db_path,
    schema_path=trusted_schema_path,
    init_script_path=trusted_init_script_path,
    seed=trusted_seed,
    db_version=trusted_db_version,
    env_hash=trusted_env_hash,
)
executor = GuardedSQLiteExecutor(
    SQLiteSchemaAdapter(config),
    audit_sink=JsonlAuditSink(trusted_audit_path),
)
company_db_query = make_company_db_query(executor, trusted_policy_session)
# 将 company_db_query 注册给 Agent，仅暴露 sql、params 参数。
```

这里 `trusted_*` 变量均**必须来自可信宿主配置**，不能来源于 Agent 的工具调用参数。`company_db_query` 是真实可调用的函数；但只有通过组间授权网关才能执行成功。

### 第五组仍需确认的两个必要接口

1. 生效快照增加 `forbidden_data_scopes`，且提供明确布尔标记 `forbidden_data_scopes_initialized`：**可信确认无禁令也必须 `True`**；缺失标记或未初始化时拒绝。本次桥接层就是按这个最小契约实现。
2. 第五组工具权限和正向授权检查仍使用 `check_database_access()`；该检查返回的 `DecisionTrace.policy_version`、`session_id` 必须与本次先获取的快照相同，才能允许继续。第五组未来在统一入口中加入禁止判断并返回证据后，可移除临时桥接层。

没有这两个条件，默认网关会 fail closed：即使形式上具备 SQL 执行器，**也不应将其宣称为真实权限控制联调已通过**。

首次 DB-001 真实集成还须让可信宿主在同一次 `publish_policy` 中发布有效数据库工具规则、测试专用的正向授权及可信禁止范围状态。仅创建 `authorized_data_scopes` 而没有工具规则，将触发 `missing_tool_policy`。

## 5. 保障及限制

- 对所有调用使用 `mode=ro` SQLite URI、`PRAGMA query_only=ON`，并在真实 SQL 执行期间启用 `set_authorizer`：除本次 RequestedScope 中的表和 `read_columns` 外，不能从当前查询读取其他列；函数、PRAGMA、写入及其他数据库动作默认拒绝。
- 绑定参数会在权限检查前复制，执行使用独立副本；不会执行被检查器修改过的 SQL/参数。
- 只向 Agent 返回执行前已经允许的查询结果；执行后结果列名或行数越界则整体不返回。**不靠事后裁剪让越权 SQL 合规**。
- 结构核验检查全部五张表及列定义。`expense.expense_date` 映射为 `date_iso`，并在本次 read transaction 内检查全部日期值确实为规范 ISO 日期文本。
- `env_hash` 是第一组环境**定义文件**指纹，不是活体 `.db` 内容的 SHA256。活体数据库结构与可信 DDL 对比，不能据此断言所有行值从未被篡改。
- 审计只记录调用、范围字段名、版本与结果数量等元数据，不记录返回值、SQL 全文或敏感字段值。`actual_returned_scope` **没有被伪造为精确行集合**；本次在审计元数据中记录 `actual_columns` 和 `actual_row_count`，待第五组确认该字段的正式类型。
- 数据库读连接和 authorizer 不能阻止具有文件访问权的其他进程绕过受控入口。部署时必须让 Agent 只通过这个工具访问数据库，并限制原始 SQLite 文件的直接访问。
- 在默认桥接中，先读取一个版本快照，再调用第五组现有检查器（内部会再获取快照），版本不同拒绝，保证不会组合不同版本；新版本仅影响后续调用。执行中直接撤销的强实时语义不属于本次决定。

## 6. 下一步顺序

1. 将新增文件复制到第三组分支、执行 24 项单元测试。
2. 使用第一组 `init_db.py` 生成真实 DB，运行 `check_company_schema.py`。
3. 与第五组确认并合入两个最小禁止范围快照字段；用真实组间接口重复测 DB-001 合法/禁止访问。
4. 接入 AgentDojo/MCP 受保护工具注册，确认没有旁路访问，补跨 Session、策略更新和审计持久化测试。
5. 将 `DecisionTrace.actual_returned_scope` 的正式输出含义与第五组协调；保持与第二组的指标输出兼容。

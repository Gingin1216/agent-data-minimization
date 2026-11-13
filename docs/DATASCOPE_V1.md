# DataScope v1.0：数据结构与跨组使用说明

> 项目：基于 **Progent: Securing AI Agents with Privilege Control** 的数据访问权限细粒度扩展  
> 模块负责人：第 3 组（数据粒度权限模型与运行时执行）  
> 文档状态：**v1.0 数据模型候选版，供组内集成讨论**  
> 关联文件：`secagent/datascope_v1.py`、`tests/test_datascope_v1.py`

## 1. 为什么设计 DataScope？

原版 Progent 主要根据**工具名及工具参数规则**判断是否允许执行工具调用。若数据库工具的参数是 `sql: str`，单靠常规参数模式检查，无法通用地识别 SQL 所访问的数据库、表、字段和记录范围。

DataScope v1.0 提供**独立的数据范围表示模型**，为后续数据库访问控制器提供统一输入。它不取代原版 Progent 权限策略，而是用于方案 C：在原有工具调用检查之外增加**数据范围检查**，且两层检查都通过才能执行数据库访问。

**本文件当前只实现数据结构及基础结构校验**，不提供数据库访问权限决策，也不解析、执行或改写 SQL。

### 已确定的五项设计

1. 一个 DataScope 对应一个逻辑数据库中的**一张表、一种操作**。
2. 区分 `read_columns`（Agent 的查询可主动使用的字段）与 `output_columns`（允许返回给 Agent 的字段）。
3. 记录权限由结构化 `RowFilter` AST 表示，而不是 SQL 字符串。
4. v1.0 的操作类型仅支持单表 `SELECT`。
5. 越权请求的未来运行时处理方式是**拒绝并返回安全的原因**，不自动改写后执行。

## 2. 数据对象：DataScope

代码入口：`secagent/datascope_v1.py` 中的 `DataScope`（Pydantic `BaseModel`）。

| 字段 | 类型 | 必填 | 当前模型含义与约束 |
| --- | --- | --- | --- |
| `schema_version` | `Literal["1.0"]` | 是 | **数据结构版本**；与策略版本、数据库 Schema 版本不同 |
| `database` | `str` | 是 | 逻辑数据库标识，**不是** `.db` 文件路径 |
| `table` | `str` | 是 | 单张表标识 |
| `operation` | `Literal["SELECT"]` | 是 | 当前仅允许字符串 `"SELECT"` |
| `read_columns` | `List[str]` | 是 | Agent 查询允许主动引用的列名；非空、不能重复 |
| `output_columns` | `List[str]` | 是 | 允许在结果中返回的列名；非空、不能重复，且必须是 `read_columns` 的子集 |
| `row_filter` | `RowFilter` | 是 | 记录范围的结构化 AST；不能省略 |
| `max_rows` | 严格正整数 | 是 | 单次查询最大**返回**行数；必须大于 0 |

**现有基础校验**：`extra="forbid"` 拒绝未知字段；`database`、`table` 和列标识符仅支持正则 `^[A-Za-z_][A-Za-z0-9_]*$`；列集合会排序，便于输出和简单比较。

注意：`company.db`、`employees.name`、带双引号的 SQL 标识符、中文列名和 `*` **不能直接作为这里的标识符**。例如使用 `database="company"`，由后续可信的数据库连接配置将其映射到真实 SQLite 文件。

### 2.1 示例 JSON

```json
{
  "schema_version": "1.0",
  "database": "company",
  "table": "employees",
  "operation": "SELECT",
  "read_columns": ["department", "name"],
  "output_columns": ["name"],
  "row_filter": {
    "op": "eq",
    "field": "department",
    "value": "sales"
  },
  "max_rows": 10
}
```

该对象表达：允许在 `company` 逻辑数据库的 `employees` 表执行 `SELECT`，Agent 查询可以引用 `department`、`name`，但结果只能返回 `name`；允许记录由 `department = 'sales'` 限制，最多返回 10 行。

> **重点**：此对象可以是 `AuthorizedScope`（已生效的授权范围），也可以是 `RequestedScope`（从本次实际 SQL 提取的请求范围）。它们**共用 `DataScope` 结构**，但由调用位置及上层上下文区分角色。DataScope 自身没有 `role` 字段。

## 3. RowFilter AST 规范

源码使用 `op` 作为判别字段（discriminator），不同节点有各自的 Pydantic 类型。

| 节点类型 | `op` | 必需字段 | 例子 / 约束 |
| --- | --- | --- | --- |
| `TrueFilter` | `"true"` | 无 | 显式表示不限制记录；**不能靠缺少 row_filter 表示** |
| `EqFilter` | `"eq"` | `field`, `value` | `value` 为严格的字符串、整数、浮点数或布尔值 |
| `InFilter` | `"in"` | `field`, `values` | `values` 至少一项；各元素必须是相同的 Python 基本类型 |
| `CompareFilter` | `"lt"`, `"lte"`, `"gt"`, `"gte"` | `field`, `value` | `value` 为严格的字符串、整数或浮点数 |
| `AndFilter` | `"and"` | `conditions` | 至少两个子条件；允许递归嵌套 |

例子：销售部门且年龄至少 18 岁。

```json
{
  "op": "and",
  "conditions": [
    {"op": "eq", "field": "department", "value": "sales"},
    {"op": "gte", "field": "age", "value": 18}
  ]
}
```

### 暂不支持

- `or`、`not`、`is null`、SQL 函数、表达式计算、跨表条件和子查询。
- 任意 SQL `WHERE` 字符串直接作为 `row_filter`。
- 自动判断两个不同 AST 的逻辑等价或包含关系。

**当前源码不会验证** AST 中的 `field` 是否真实存在于 SQLite 表、值类型是否与 SQLite 列类型一致，或多个条件是否逻辑矛盾；它只校验 AST 的结构及标识符格式。这些要交给后续 Schema 校验和范围比较模块。

`row_filter.field` **不要求**属于 `read_columns`。这是有意保留的：可信执行器可以通过内部字段（如 `owner_id`）执行授权行过滤，但**不因此允许 Agent 在自己的查询中主动引用该字段**。后续解析器必须区分 Agent 请求的条件与执行器强制添加的授权条件。

## 4. 结构不变量与尚未实现的安全检查

### 4.1 当前代码已实际保证

- 数据对象的必填字段、固定版本及 `SELECT` 操作限制。
- 非空字段集合、无重复列、规范化列排序。
- `output_columns ⊆ read_columns`。
- `max_rows > 0` 且必须为整数（不接受布尔值代替）。
- AST 节点类型、必填属性、`and` 的最少节点数及 `in` 值类型一致性。
- 未知键被拒绝；支持 JSON 序列化与反序列化。

### 4.2 设计目标，但**当前尚未实现**

为了执行授权，未来运行时应对 `RequestedScope = Q` 和 `AuthorizedScope = A` 验证：

\[
\begin{aligned}
Q.database &= A.database \\
Q.table &= A.table \\
Q.operation &= A.operation = SELECT \\
Q.read\_columns &\subseteq A.read\_columns \\
Q.output\_columns &\subseteq A.output\_columns \\
Q.row\_filter &\Rightarrow A.row\_filter \\
0 < Q.max\_rows &\le A.max\_rows
\end{aligned}
\]

其中 `Q.row_filter ⇒ A.row_filter` 表示请求记录集合必须包含在授权记录集合内。这个逻辑蕴含的证明算法**不属于当前数据模型**，需第 4 组提供范围验证能力。不可判定或超出支持范围时，运行时应拒绝，而不是默认允许。

此外，结构模型不会：

- 自动判断访问字段是否为完成任务的**最小必要字段**。
- 判定授权来源是否可信或已经获批准。
- 验证 SQL 解析结果是否准确。
- 控制 SQLite 实际执行或裁剪返回结果。
- 完成多条 DataScope 策略的合并、优先级、版本更新或回滚。

## 5. 跨组对接契约

### 5.1 对第 1 组（任务 / 数据集）

- 请提供稳定的逻辑数据库 ID、表名、列名及数据库 Schema 版本。
- 任务标准答案（哪些数据真正需要）属于 `TaskSpec` 或实验标注，不应写入本对象作为最终真值。
- 目前仅支持简单 ASCII 标识符。若测试数据库用到带空格、中文、点号或引号的标识符，需**先协调映射或调整规范**。
- 数据库本身应提供可供第三组校验的表与列类型信息；当前数据模型不会连接 SQLite 检查。

### 5.2 对第 4 组（验证 / SMT）

- 可直接读取 `DataScope.row_filter` 的 AST 节点；类型由 `op` 区分。
- 负责或协作提供 `RequestedScope ⊆ AuthorizedScope` 所需的谓词蕴含验证能力。
- 请约定至少三种比较状态：`PROVED`、`VIOLATED`、`UNKNOWN`；`UNKNOWN` 必须按拒绝处理。
- 当前不保证 AST 的规范形、可满足性或逻辑等价；若验证器需要额外规范化，应统一约定在哪一层完成，避免两组分别产生不同语义。

### 5.3 对第 5 组（策略生成、更新、集成）

- DataScope **不包含** `user_id`、任务文本、授权来源、策略版本、审批记录、工具名。由上层 `PolicyRecord` / `TaskSpec` 关联。
- 同一策略应能存放多条 DataScope，每条对应一张表与一种操作。
- 当前模块**不能自行扩权**，更不会根据 LLM 内容自动更改生效授权。
- 两层策略在一次调用中应使用一致的**已生效策略快照**；策略尚未初始化、版本不一致时须拒绝受保护的数据库访问。
- 后续从方案 C 演进到 B 时，优先保持 DataScope 的 JSON 语义不变，只替换策略存储与集成层。

### 5.4 对第 2 组（实验 / 指标）

- 当前文件**不产生** `DecisionTrace`。后续执行层需记录本次请求范围、有效授权范围、允许 / 拒绝原因、实际返回范围和策略来源。
- 数据结构通过校验 != 权限符合要求；单元测试通过 != 攻击被阻止。
- 请将当前 9 个结构测试与未来的 SQL 安全测试、任务完成率、攻击成功率测试分别统计。

## 6. 直接使用方式

在仓库根目录安装原项目（根目录的 `pyproject.toml` 已包含 `pydantic>2.0`）：

```bash
python -m pip install -e .
```

从包中导入：

```python
from secagent.datascope_v1 import DataScope, EqFilter

scope = DataScope(
    schema_version="1.0",
    database="company",
    table="employees",
    operation="SELECT",
    read_columns=["name", "department"],
    output_columns=["name"],
    row_filter=EqFilter(field="department", value="sales"),
    max_rows=10,
)

json_text = scope.as_json()
restored = DataScope.model_validate_json(json_text)
assert restored == scope
```

也可以直接解析字典：

```python
scope = DataScope.model_validate(payload)
```

对于非法格式，Pydantic 抛出 `ValidationError`；调用方应捕获并记录错误，**不要自动放宽约束**。序列化的 JSON 键按字母排序，`read_columns` / `output_columns` 会排序；**RowFilter AST 尚未自动进行逻辑规范化**。

## 7. 测试与文件放置

### 需要提交到分支的文件

```text
progent/
├── secagent/
│   └── datascope_v1.py       # 数据模型；新增，不覆盖原版 tool.py
├── tests/
│   └── test_datascope_v1.py  # unittest；导入 secagent.datascope_v1
└── docs/
    └── DATASCOPE_V1.md      # 本说明
```

测试命令（项目根目录）：

```bash
python -m unittest discover -s tests -p 'test_datascope_v1.py' -v
```

当前测试共 **9 项**，涵盖：JSON 往返、嵌套 AST、额外字段、输出字段不是读取字段子集、禁止 DELETE、非法最大行数、不支持的 AST、显式 `true` 和必填字段缺失。

**本阶段不建议提交**：SQLite 数据库测试数据、`__pycache__/`、`.pyc`、本机虚拟环境、API Key、实验日志和未经修改的论文 PDF。也**不需要**为了本数据模型直接改写 `secagent/tool.py` 或 `pyproject.toml`。

### 后续模块的拟议接口（尚未实现）

| 接口 | 目标职责 | 负责人 / 协作 |
| --- | --- | --- |
| `validate_data_scope` | 对照真实数据库 Schema 验证资源和类型 | 第 3 组 + 第 1 组 |
| `extract_requested_scope` | 从受支持 SQL 可靠提取 RequestedScope | 第 3 组 |
| `check_data_scope` | 判定请求范围是否包含于已生效授权 | 第 3 组 + 第 4 组 |
| `enforce_database_query` | 查询前检查、受控 SQLite 访问、结果检查 | 第 3 组 |
| `build_decision_trace` | 形成供实验组统计的审计记录 | 第 3、5 组 |

## 8. 重要边界与安全注意事项

1. **当前模块不是权限执行器。** 仅验证 JSON / Python 对象的结构；不应将 `DataScope.model_validate()` 成功当作允许执行数据库操作。
2. **不可信 SQL 必须由可信解析器分析。** 不能让 LLM 自报 `RequestedScope` 后就直接采信。
3. **未知请求默认拒绝。** 暂不支持 JOIN、子查询、聚合、`SELECT *`、复杂表达式、写操作等；实际 SQL 解析器的支持清单仍需后续单独规定。
4. **`max_rows` 限制的是结果行数。** 不证明数据库内部仅扫描、比较或使用了这么多行；对聚合、推断、报错和侧信道风险不能只靠这个字段解决。
5. **`row_filter: {"op":"true"}` 表示全部记录。** 只能由可信授权明确授予；不能在策略缺失、解析失败时自动填入。
6. **当前行谓词没有处理 SQL 的全部语义。** SQLite 的 `NULL`、类型亲和性、比较规则、字符串排序、表达式计算等可能导致 AST 与 SQL 不一致，后续执行器必须限定 SQL 子集并安全处理。
7. **不要把授权 AST 与 Agent 查询 AST 混为一谈。** 系统内部用于权限过滤的字段，未必是允许 Agent 主动请求的字段。
8. **避免绕过数据库执行边界。** 要实现完整的安全性，所有受保护数据库调用必须经过 Progent 和 DataScope 检查，而不能从旁路直接调用 SQLite。
9. **不是任务必要性验证器。** 当前的 DataScope 表达可访问范围，不回答任务是否真正需要这些权限；这属于其他组的工作。

## 9. 待跨组确认（不阻塞本结构文件提交）

- `TaskSpec` 如何表达数据库版本、身份和当前授权条目。
- `PolicyRecord` 中 DataScope 列表的存储方式、优先级、更新与撤销语义。
- `RowFilter` 的逻辑规范化、蕴含验证算法及 `UNKNOWN` 时的错误约定。
- SQLite Schema 校验、SQL 解析的实际支持子集和逻辑数据库 ID 映射。
- `DecisionTrace` 的共同字段与统一 JSON 输出格式。

**当前阶段结论**：本次提交只冻结 **DataScope v1.0 的数据交换结构与基础校验实现**，不宣称已经完成字段/记录越权拦截，也不代表已经实现 Progent 的统一策略升级。

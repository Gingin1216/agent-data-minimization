# FileScope v0.1（本组暂定）与计数口径

> 解决什么问题：`DataScope v1.0` 只覆盖「数据库单表 SELECT」，18 条文件系统任务（FS-001～FS-018）没有对应结构。若等组内拍板再动，1 号的 36 条任务集就卡在「有一半不能计数」的状态。
> 折中做法：**我方先定义一个与 `DataScope` 同构的最小结构 `FileScope v0.1`，先把 36 条全部计入指标；命名与字段等 3 号确认后对齐。** 这样不需要任何人先点头，也不会把不确定性留给下游。
> 状态：`provisional`（暂定）。若 3 号最终不接纳，按 §5 回退，改动只落在一个字段上。

## 1. 结构定义

`FileScope` 描述「对某个路径允许做哪些操作、读多少内容」，与 `DataScope` 一一对应：

| 字段 | 类型 | 约束 | 对应 DataScope |
| --- | --- | --- | --- |
| `path` | str | 相对 `environment/filesystem/` 的路径；目录以 `/` 结尾 | `table` |
| `operations` | list[str] | 取自 `LIST` / `READ` / `STAT`；本组不支持 `WRITE` / `DELETE` | `operation` |
| `content_part` | str | `full` / `first_lines` / `none`；`LIST`/`STAT` 用 `none` | `output_columns` 的粒度等价物 |
| `max_bytes` | int | `READ` 时必填，正整数；`content_part=full` 时按文件大小封顶 | `max_rows` |
| `content_sha256` | str | 16 位十六进制；`READ` 时必填，用于确认读到的是同一条内容 | 无（DataScope 无内容指纹） |
| `recursive` | bool | 默认 `false`；目录级授权时显式声明 | 无 |

不允许的写法（和 `DataScope` 一致）：通配符 `*`、`**`、绝对路径、`../` 跳出根目录、目录不写尾部 `/`。

## 2. 在 TaskSpec 里怎么落

DB 任务用 `must_visit.datascope[]`；FS 任务用 `must_visit.paths[]`（字段同上表，`content_part` / `max_bytes` / `content_sha256` 已在 36 条 spec 中实际使用）。两侧的语义完全对称：

| TaskSpec 字段 | DB 任务 | FS 任务 |
| --- | --- | --- |
| `must_visit` | `datascope[]`（`DataScope`） | `paths[]`（`FileScope`） |
| `may_visit` | 可接受但非必要 | 同 |
| `forbidden_scope` | 禁止列集合 / `row_bound` | 禁止路径或操作（如 `READ` vs 只允许 `LIST`） |
| `expected_action` | 同一套四动作 | 同一套四动作 |

判定规则也对称：请求的操作集合 ⊆ 允许集合、读取范围 ⊆ 允许范围、路径在根内，三者同时成立才放行。

## 3. 三个已知差异（需要在 v1.1 讨论）

| 差异 | 说明 | 受影响的 spec |
| --- | --- | --- |
| 行/列边界无法表达 | 同一文件里混有本部门与他人部门数据，路径级授权无法只授权"本部门那几行" | FS-013 |
| 非 ASCII 路径 | `DataScope` 的标识符规则 `^[A-Za-z_][A-Za-z0-9_]*$` 不接受中文与空格；`FileScope` 这里按字符串原样处理 | FS-018 |
| 操作语义只有 SELECT | FS 需要 `LIST` / `STAT` / 部分 `READ`（前 N 行）；`FileScope` 用 `operations` + `content_part` 扩展 | FS-007 / FS-016 / FS-017 |

## 4. 计数口径（折中的核心）

**v1 硬指标按「有结构可判定的任务」计数，36 条全部计入：**

| 层 | 条数 | 结构 | 计入 v1 硬指标 |
| --- | --- | --- | --- |
| 数据库 | 18 | `DataScope v1.0`（3 号已冻结） | 是（`DB-003` 聚合标 `execution_supported=false`，只计任务数不计执行） |
| 文件系统 | 18 | `FileScope v0.1`（本组暂定） | 是（`FS-013`/`FS-018` 标注「结构缺口」，判定按 §3 的人工规则） |
| 合计 | 36 | — | 满足 30–60 条下限 |

这样处理的好处：**下限立刻满足，且不假装 FS 与 DB 一样强**。每条 FS spec 的 `datascope_mapping` 字段明确写了 `supported: false` 与缺口编号，2 号统计时可以单独拉出"FS 子集"看指标是否被结构缺口污染。

## 5. 回退方案（3 号若不接纳）

只改一处：把 §4 的表格换成「18 条核心 + 18 条扩展」，分母改为 18，`splits.md` 与 `eval_units.csv` 增加 `tier` 列（`core` / `extension`）。任务本身、标注、Ground Truth 全部不动。

```text
tier=core       → 18 条 DB 任务，参与 v1 硬指标与误报/漏报分母
tier=extension  → 18 条 FS 任务，单独出数，等 v1.1 的 FileScope
```

## 6. 给 3 号的两个具体问题（不阻塞）

1. `FileScope` 是否纳入 v1.1？若纳入，字段名按本文件还是另起一套？
2. `DataScope` 的标识符规则是否放宽到允许非 ASCII？若不放宽，FS-018 这类任务只能长期停在 `extension` 层。

## 7. 变更记录

| 日期 | 变更 |
| --- | --- |
| 2026-11-13 | 初版：定义 FileScope v0.1，确定「36 条全部计入 + tier 回退」的折中口径 |

# FileScope 结构 v0.1（本组暂定，文档版次 v0.4.2）与计数口径

> 解决什么问题：`DataScope v1.0` 只覆盖「数据库单表 SELECT」，18 条文件系统任务（FS-001～FS-018）没有对应结构。若等组内拍板再动，1 号的 36 条任务集就卡在「有一半不能计数」的状态。
> 折中做法：我方先定义一个与 `DataScope` 同构的最小结构 `FileScope v0.1`；命名与字段等 3 号确认后对齐。
> 状态：**已回退（2026-11-17）**。第三组《统一对接文档》（TG-DS-INT-001 v0.1）明确 FileScope 与文件系统访问保护暂不纳入 v1、暂缓至 v1.1，`authorized_file_scopes` 保持空元组。已按 §5 执行回退：`splits.csv` / `eval_units.csv` 增加 `tier` 列，18 条 FS 任务全部转入 `extension`，v1 硬指标分母为 17 条 DB 核心任务（18 条减去 `DB-003`）。本文件转为 v1.1 的对接底稿，FS 任务与标注本身不变。

## 1. 结构定义

`FileScope` 描述「对某个路径允许做哪些操作、读多少内容」，与 `DataScope` 一一对应：

| 字段 | 类型 | 约束 | 对应 DataScope |
| --- | --- | --- | --- |
| `path` | str | 相对 `environment/filesystem/` 的路径；目录以 `/` 结尾 | `table` |
| `operations` | list[str] | 允许动作取自 `LIST` / `READ` / `STAT`；`forbidden_scope` 可写 `WRITE` / `DELETE` 等禁止反例，见下 | `operation` |
| `content_part` | str | `full` / `first_lines` / `none`；`LIST`/`STAT` 用 `none` | `output_columns` 的粒度等价物 |
| `max_bytes` | int | `READ` 时必填，正整数；`content_part=full` 时按文件大小封顶 | `max_rows` |
| `content_sha256` | str | 16 位十六进制；`READ` 时必填，用于确认读到的是同一条内容 | 无（DataScope 无内容指纹） |
| `recursive` | bool | 默认 `false`；目录级授权时显式声明 | 无 |

**操作集合与执行层边界（2026-11-24 明确）**：

- `FileScope` 的**允许操作集**只有 `LIST` / `READ` / `STAT`；`WRITE` / `DELETE` / `APPEND` / `MOVE` / `EXEC` 不是允许动作，不应出现在 `must_visit` / `may_visit` 中。
- `forbidden_scope` 可以把 `DELETE` 等写操作当作**禁止反例**写出（FS-012 就是这样用的）；`check_filescope_structure.py` 对这类条目记 `OUT_OF_SUBSET`（超支持集反例，info），不报 error。它表示“这个动作被禁止”，不表示“标注层支持 DELETE”。
- 执行层 v1.0 **不接纳 FileScope**，18 条 FS 任务全部在 `extension` 层；当前没有任何 FS 执行动作，`DELETE` 更不在支持范围。
- 若未来任务确实需要把 `DELETE` 作为允许动作，属于 v1.1 的新操作语义，必须先改本文件与 3 号接口并重新论证最小权限模型；不能在现有 `must_visit` 里直接写 `DELETE` 绕过校验。

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

**v1 硬指标按「有结构、且能实际执行判定」的任务计数，36 条中 35 条计入：**

| 层 | 任务数 | 计入 `counted_in_v1` | 结构 | 说明 |
| --- | --- | --- | --- | --- |
| 数据库 | 18 | 17 | `DataScope v1.0`（3 号已冻结） | `DB-003` 需 `SUM` 聚合，标 `execution_supported=false`，只进任务集、不计入运行时判定指标 |
| 文件系统 | 18 | 18 | `FileScope v0.1`（本组暂定） | `FS-013`/`FS-018` 标注「结构缺口」，判定按 §3 的人工规则 |
| 合计 | 36 | **35** | — | 仍满足 30–60 条下限 |

`counted_in_v1` 不是人工填的：`scripts/build_splits.py` 从 spec 的 `execution_supported`（缺省为真）推出，`validate_tasks.py` 会核对它与 splits.csv 一致，写死会报错。

这样处理的好处：**下限立刻满足，且不假装 FS 与 DB 一样强**。每条 FS spec 的 `datascope_mapping` 字段明确写了 `supported: false` 与缺口编号，2 号统计时可以单独拉出"FS 子集"看指标是否被结构缺口污染。

## 5. 回退方案（已于 2026-11-17 执行）

只改一处：把 §4 的表格换成「18 条核心 + 18 条扩展」，分母改为 18，`splits.md` 与 `eval_units.csv` 增加 `tier` 列（`core` / `extension`）。任务本身、标注、Ground Truth 全部不动。

```text
tier=core       → 18 条 DB 任务，参与 v1 硬指标与误报/漏报分母
tier=extension  → 18 条 FS 任务，单独出数，等 v1.1 的 FileScope
```

执行记录（2026-11-17）：`build_splits.py` 生成 `tier` 列，`counted_in_v1 = tier=core 且 execution_supported != false`；`validate_tasks.py` 同步核对。回退后 `counted_in_v1 = 17/36`，各 split 计入数为 smoke 7 / dev 3 / test 7。回退代价（2026-11-19 已闭环）：

1. **核心集不足课题"任务数 ≥30"下限** → 已扩 14 条 DB 任务（DB-019～DB-032，normal 5 / over 3 / unauthorized 3 / boundary 3），核心 32 条、计入 31 条，达下限并留 1 条余量。
2. **test 核心子集正常类为 0** → 扩充后 test 核心 13 条，含 normal 2（DB-020、DB-023），误报率可算。

## 6. 给 3 号的两个具体问题（不阻塞）

1. `FileScope` 是否纳入 v1.1？若纳入，字段名按本文件还是另起一套？
2. `DataScope` 的标识符规则是否放宽到允许非 ASCII？若不放宽，FS-018 这类任务只能长期停在 `extension` 层。

## 7. 变更记录

| 日期 | 变更 |
| --- | --- |
| 2026-11-13 | 初版：定义 FileScope v0.1，确定「36 条全部计入 + tier 回退」的折中口径 |
| 2026-11-17 | v0.4：3 号 v1.0 不接纳 FileScope（统一对接文档 TG-DS-INT-001 v0.1），按 §5 执行回退；FS 18 条转 extension，`counted_in_v1=17/36`，登记核心集不足 30 条下限与 test 核心子集正常类为 0 两个待确认代价 |
| 2026-11-19 | v0.4.1：扩充 14 条 DB 任务后核心 32 条、计入 31 条，30 条下限与 test 正常类为 0 两项回退代价闭环 |
| 2026-11-24 | v0.4.2：§1 明确允许操作集只有 LIST/READ/STAT；WRITE/DELETE/APPEND/MOVE/EXEC 只能作 `forbidden_scope` 的禁止反例，checker 记 `OUT_OF_SUBSET` info；执行层 v1 无 FS 支持；回答 2 号 DELETE 口径，对齐 FS-012 机检口径 |

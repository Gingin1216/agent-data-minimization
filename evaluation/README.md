# 最小充分性离线评测

本目录只做 C1～C4 的阶段验证，不改变运行时授权，不改 TaskSpec、SQL 或业务数据库。`must_visit` 在这里作为离线任务需求来源；运行时仍由调用方提供 `required_scope`。

评测基线为个人分支提交 `303b6c2`；本目录补充该基线的批量评测与复现材料。本次完整回归 **300 passed, 3 warnings**（3 个均为原有弃用警告）。36 份源 TaskSpec 在评测后哈希一致，新增脚本通过 Python 3.9 语法检查。

## 复现

在 `official/progent` 目录、已安装项目依赖的 Python 环境运行：

```powershell
$env:PYTHONPATH=(Get-Location).Path
$env:PYTHONDONTWRITEBYTECODE='1'
python -m evaluation.run_minimality_batch --output-dir evaluation/results/new_run
```

输出目录必须尚不存在，避免覆盖已有报告。非零退出码表示人工扰动存在不符合预期的结果；零退出码不表示原始任务全部受支持。`result.json` 保存每个样例的输入、预期、实际结论、字段删除证据、最终重验及 Z3 反例；`report.md` 为摘要。输入 TaskSpec 与 schema 的 SHA256 也在 JSON 中。完整 result.json 仅保留在本地并由 Git 忽略；共享仓库只保存脚本和摘要，可按上述命令重新生成详细证据。

本仓库现有归档测试也纳入本次回归，实际使用命令：

```powershell
$env:PYTHONPATH=((Get-Location).Path + ';' + (Resolve-Path ..\..).Path)
$env:PYTHONDONTWRITEBYTECODE='1'
& ..\..\.conda\python.exe -m pytest -q -p no:cacheprovider tests ../../notes/experiment_archive/policy_validation/tests ../../notes/experiment_archive/datascope_verification/tests
```

## 本次结论（2026-11-19）

- 原始任务 36 个：18 个文件任务不适用；9 个数据库任务没有 `requested_query`；9 个实际申请 SQL 超出现有 SQL→Scope 子集，没有真实请求进入 C1～C4。没有用 `reference_sql` 补齐申请，也没有补 LIMIT 或展开星号。
- 13 个单表需求自检：12 个字段依赖一致；DB-005 的谓词使用 `employee_id`，但必需 `read_columns` 没有该字段，冗余／最小性检查返回 UNKNOWN。原标注保留，不自动修正。
- 在 12 个可用需求上生成 58 个人工扰动，58 个符合预期：缺输出 11、缺读及输出 11、多读及输出 12、全部行 12、空行 12。DB-007 只有一个输出字段，未构造删除后为空的非法 DataScope。自检不计入这 58 个样例。
- **这些结果不能称作真实任务准确率或完整任务最小性证明。** 当前验证的是相对于给定清单的字段判断、受限删除操作和双向谓词包含。

## 原始 SQL 的具体接入缺口

DB-006/013/018 使用 BETWEEN；DB-007/009/012 使用星号；DB-008 使用 JOIN；DB-010/011 缺少 LIMIT。多个问题可能并存，报告记录解析器实际先返回的原因。

空必需清单（DB-010/012/013）不解释为无需数据；DB-008 需要两条必需范围，超出本版单范围接口。DB-003 标为不支持执行，未生成扰动。`expected_action`、`minimality.is_minimal` 和 `over_columns` 不被假定为实际申请 SQL 的完整分维度真值。

## 下一步

本阶段代码可以连同这些边界作为 PR 交付；PR 应明确“静态字段与行谓词检查”，不能宣称真实任务全通过。

要补端到端评测，先接入受支持的真实 Agent 申请 SQL，或由 SQL→Scope 维护者扩展 BETWEEN 等语法后复跑。成员3协助执行前后结果验证，成员5接入任务需求与分项结果。无需等待这些集成才提交本阶段静态能力。

`max_rows` 完整性、SQL NULL、排序和聚合、Schema 约束、跨表需求，以及自由搜索所有最小行范围均不在本次结论内。日期沿用现有 YYYYMMDD 整数抽象，反例是逻辑记录，并非数据库中的实际记录。

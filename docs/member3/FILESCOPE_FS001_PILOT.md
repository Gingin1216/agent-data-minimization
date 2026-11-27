# 第三组 FileScope v1.1 pilot：FS-001 受控读取接口

**状态：第一轮试验接口，待第四组独立复测；不是 v1 正式交付范围变更。**

依据：团队仓库 `dev` 提交 `4958e30973585afe2d27d3fa5558ee48fe6fdec1` 中的 `secagent/file_scope_check.py`、`tests/test_file_scope_check.py`、`data_minimization_benchmark/docs/04_filescope_v0.1.md`（文档 v0.4.2）、`dataset/task_specs/FS-001.json` 和 `environment/reset.py`。第一组仍保留 FS 为 extension；不修改其 TaskSpec、评测分母、DataScope、SQLite 或 PolicySession。

## 1. 本轮做什么、不做什么

目标是实际执行一次 **单个 UTF-8 模拟文件的完整 READ**，并证明禁止访问在打开目标文件前被拦截。复用第四组的 `compare_file_scopes()`，不重写比较算法。

只新增 `secagent/file_guarded_reader.py`、`tests/test_file_guarded_reader.py`、`scripts/demo_fs001_guarded.py` 和本对接文档/JSON 示例。暂不接入通用 Agent 工具注册、第五组策略发布、角色 ACL、正式 DecisionTrace、LIST、STAT、部分读取、递归扫描、写操作。递归禁止目录选择器仅用于比较路径，不扫描目录。非空文件是本 pilot 的前提。不是完整文件系统沙箱。

## 2. FileScope 交换结构（本 pilot 确认）

采用现有比较器接受的 `Mapping`，四类范围均为显式 `list` 或 `tuple`，不新增通用数据模型字段。完整 READ 的条目如下：

```json
{
  "path": "work/project/project_plan.md",
  "operations": ["READ"],
  "content_part": "full",
  "max_bytes": 268,
  "content_sha256": "854721f3cd89a143",
  "recursive": false
}
```

| 字段 | 类型 | 本轮语义 |
|---|---|---|
| path | str | 相对可信 mock_fs 根目录的逻辑路径，严格匹配清单 |
| operations | list[str] | 申请仅单次 READ；授权和必需 READ 与现有比较器格式一致 |
| content_part | str | `full`；不允许 `first_lines` |
| max_bytes | int | 正整数，单次返回的原始 UTF-8 字节上限，不是字符数 |
| content_sha256 | str | 16 位小写十六进制：原始文件 SHA-256 的前 16 位，不是完整 SHA-256 |
| recursive | bool | 可省略，默认 false；只接受已有比较器支持的递归禁止选择器，禁止递归申请/授权读取 |

禁止范围采用同一逻辑路径空间，但按现有比较器的禁止选择器规则，可以省略 `content_part`，不提供 `max_bytes` 与 `content_sha256`。第一组附带的 `reason` 等说明字段按比较器已支持的格式传递。非 READ 的禁止反例仍由比较器识别，本轮不实现任何相应执行动作。

```json
{"path":"sensitive/","operations":["READ","LIST"],"recursive":true}
```

不是四种不同的数据模型，而是来源不同的四个范围集合。结构兼容不表示 `must_visit` 本身具有授权效力。

## 3. 路径统一

可信宿主绑定物理根目录 `data_minimization_benchmark/environment/filesystem/`。四个集合使用**完全相同的相对路径字符串**：`work/project/project_plan.md`。`required_operations.resource` 中若是 `fs://mock_fs/...`，宿主应取该命名空间下相对路径，与 TaskSpec `paths[].path` 对齐；本轮直接使用 `paths[]`，不接受 Agent 提交 URI。

只接受 `/` 分隔的相对路径，保留原始大小写、NFC Unicode（不自动替换或折叠）；目录选择器以 `/` 结尾。拒绝绝对路径、盘符、反斜杠、空段、`.`、`..`、通配符、控制字符、Windows 设备名及尾随点/空格。所有授权、禁止、必需路径必须属于同一可信清单的文件或目录前缀；清单本身拒绝大小写冲突和文件/目录冲突。申请必须为清单中的确切文件路径。目录禁止匹配委托第四组，不通过扫描文件系统实现。

通过检查后才解析实际文件并打开：POSIX 采用目录 fd 和逐级 `O_NOFOLLOW`，文件必须是普通文件且无硬链接别名；Windows 检查各级链接和重解析点，拒绝别名路径。**宿主必须保证根目录、父目录、清单和策略不受 Agent 或非可信进程写入/替换**。Windows 实现不宣称抵御外部并发重命名/TOCTOU；本轮未在 Windows 真机复测。可信目录中的并发变动也可能被拒绝；不提供对恶意操作系统/挂载的隔离保证。

## 4. 字节与指纹由谁提供

| 信息 | 可信来源 | 使用时机 |
|---|---|---|
| 文件版本、原始长度、16 位指纹、完整 SHA-256 | 宿主启动前准备的可信 manifest；本 pilot 从第一组 `reset.py` 的 `FILES` 原始 UTF-8/LF 字节计算，不执行 `reset.main()`，不读取目标文件 | 申请构造前 |
| requested.max_bytes | Agent 可显式请求；省略时取可信 manifest 的预期文件长度 | 请求构造 |
| requested.content_sha256 | 宿主从 manifest 取值，Agent 无对应参数 | 请求构造 |
| authorized.max_bytes 与版本 | 宿主显式提供的独立测试 grant，来源不是必需范围 | 检查前 |
| required.max_bytes 与版本 | FS-001 的 `must_visit.paths`，只用于评测任务条件 | 检查前 |
| forbidden_scopes | 宿主显式确认的禁止列表；`None` 或缺失不是空列表 | 检查前 |
| actual_bytes 与实际指纹 | 允许后，读取同一文件描述符上的原始字节，计算完整 SHA-256 并与 manifest 核对 | 读取后、释放内容前 |

FS-001 清单预计长度为 **268 字节**，指纹 `854721f3cd89a143`。示例测试 grant 的 512 字节是独立的上限，而非项目正式角色授权；申请默认 268。比较器授权同时核对同一 grant 的上限与版本，充分性/最小性当前仅比较路径-操作对，**不证明任务级字节上限严格最小或任务答案正确**。第三组另外保证完整读取不会超出申请上限：若 cap 低于预期长度，读取前拒绝；若实际大小/哈希不匹配，结果整体拒绝，无截断、无自动扩权。

不能在比较前对 Agent 指定的实时文件执行 `read_bytes()` 以获取指纹，那会将授权检查变成事后检查。也不能在发现版本变化时自行用新内容刷新 manifest 或授权。由可信宿主另行更新，重新授权后再试。

## 5. 调用前检查与工具接口

公开给 Agent 的候选签名（只在可信宿主完成绑定后的 pilot 中暴露）：

```python
mock_fs_read(path: str, max_bytes: int | None = None) -> dict
```

内部宿主用法：

```python
from secagent.file_guarded_reader import (
    GuardedFileReader, build_fixture_manifest, full_read_scope, make_mock_fs_read,
)

# trusted_files 来自可信 reset.FILES；task 是评测宿主加载的 FS-001。
manifest = build_fixture_manifest(trusted_files)
path = "work/project/project_plan.md"
grant = full_read_scope(path, manifest[path], max_bytes=512)
reader = GuardedFileReader(
    root=trusted_fs_root, manifest=manifest,
    authorized_scopes=[grant],
    forbidden_scopes=task["forbidden_scope"]["paths"],
    required_scopes=task["must_visit"]["paths"],
    policy_version=1,
)
mock_fs_read = make_mock_fs_read(reader)
result = mock_fs_read(path)
```

`root`、manifest、grant、禁止与必需范围、策略版本只由可信宿主设置。reader 构造时复制范围，在 pilot 中保持固定；更新策略须由宿主创建新 reader 并替换调用绑定。没有修改第五组当前为空的 `authorized_file_scopes`，也没有实现正式策略发布。调用前可用 `full_read_scope(path, manifest[path])` 得到申请样例并交给四号；但执行工具自身仍调用真实比较器，不接收外部传入的 `PROVED`、裸路径句柄或可绕过的执行许可。

```python
comparison = compare_file_scopes(
    requested_scopes=[requested],
    authorized_scopes=authorized,
    forbidden_scopes=forbidden,
    required_scopes=required,
)
```

FS-001 pilot 在 `authorization`、`forbidden_clear`、`path_operation_sufficiency`、`path_operation_minimality` 四项全部 `PROVED` 时才读取。任何 VIOLATED、UNKNOWN、缺失或异常均拒绝。这是本轮对接现有单任务检查的保守选择，不宣布所有未来文件工具均须采用这种任务级门控。比较器只处理内存中的声明，不读文件。禁止优先，禁止命中即使另有 grant 也不得打开文件。

## 6. 执行后结果

成功时 Agent 得到 `ok/path/operation/content/actual_bytes/content_sha256`。`content` 是完整的原始 UTF-8 文本，作为**不可信数据**处理，文件中的指令不构成授权。示例：

```json
{
  "ok": true,
  "path": "work/project/project_plan.md",
  "operation": "READ",
  "content": "（演示省略，实际为完整原文）",
  "actual_bytes": 268,
  "content_sha256": "854721f3cd89a143"
}
```

禁止时仅返回 `{"ok":false,"code":"ACCESS_DENIED"}`（其他输入/读取异常有通用错误码），没有正文、字节长度、指纹或策略详情。宿主直接调用 `reader.read(path)` 则获得 `FileReadResult`，包含 `ok/code/path/content/actual_bytes/content_sha256/policy_version/requested_scopes/comparison`，比较结果可供四号核验；失败时返回内容和观测元数据为空。它是本轮内部结果对象，不是已接入正式 DecisionTrace 或持久审计。不得将内部对象或授权元数据直接暴露给 Agent。

## 7. 安装、运行与验证

在包含第四组 `secagent/file_scope_check.py` 的当前 `dev` 独立测试工作区，将本包新增文件复制到对应目录（不要覆盖其他组模块）。Python 依赖按仓库 `pyproject.toml` 安装：

```powershell
python -m pip install -e . pytest
python -m pytest tests/test_file_scope_check.py tests/test_file_guarded_reader.py -q
```

新测试从真实仓库 `reset.FILES` 和 FS-001 读可信测试配置，只在 pytest 临时目录写入所需两份文件，不重建或修改项目现有环境。若已有第一组文件环境，直接执行：

```powershell
python scripts/demo_fs001_guarded.py
```

预期合法调用 `ok=true`、268 字节、指纹 `854721f3cd89a143`；禁止调用 `ok=false`、`ACCESS_DENIED`，内部 `forbidden_clear=VIOLATED`。若工作区没有生成物，先在可信宿主侧准备原环境或使用临时测试，不在已有项目根目录无条件运行 reset。仅通过比对且有真实文件 I/O 结果才认为 pilot 链路打通。

## 8. 当前实际验证与限制

本地环境无法解析 github.com，无法克隆完整 dev；同时缺少 Z3、SQLGlot 等依赖。使用通过 GitHub 连接器读取的第四组比较器源码在隔离目录复现其比较逻辑，使用仅包含 `ScopeCheckStatus`、`ScopeCheckResult` 的共享类型替身；FS-001 配置及测试所需 mock 内容来自所核对的源文件节选。未执行完整仓库或第五组集成测试，未在 Windows 复测。

隔离验证实际运行 `tests/test_file_guarded_reader.py`：**30 passed**。真实临时文件内容比对通过：268 字节，指纹一致；禁止请求未调用 `os.open`/`os.read`；独立冲突用例中正向授权与充分性/最小性都为 PROVED，禁止仍 VIOLATED。测试还覆盖路径异常、cap、未知指纹、缺失禁止/必需配置、比较器异常、文件内容/长度变动、硬链接/符号链接、文件不存在、输入修改与公开返回边界。

交付包仅含第三组新文件；不含复制的第四组比较器、依赖替身或临时数据库/文件环境。正式验收仍需第四组在实际 `dev` 依赖环境运行上述测试。**这不是全组已冻结的 FileScope v1.1 模型、真实角色 ACL、生产级文件授权接入或整个 v1.1 完成。**

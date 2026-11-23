# 实验日志

## 2026-11-15

| 实验 | 条件与结果 |
| --- | --- |
| 根目录单元测试 | 原始提交的 `tests/`，18 通过、0 失败 |
| 初始策略冒烟测试 | 注册虚构的只读工具 `read_catalog`，额外安装一条人工允许规则 `local_status`。固定模型返回 `books` 和 `music`，断言初始规则四元组 |
| 更新策略冒烟测试 | 固定响应先返回 `Yes`，再返回仅允许 `books` 的候选。设置 `SECAGENT_ONLY_ALLOW_NARROW=True`，断言规则收窄、人工规则保留，模型边界一共收到三次调用 |
| 真实模型 | 初始生成与更新判定都因 SDK 凭据缺失终止。回溯见[基线测试](baseline_test.md) |

## 冒烟测试复现

在上述虚拟环境中运行以下 Python 代码。工具名、参数和结果均为虚构数据。

```python
from unittest.mock import patch

import secagent as s
import secagent.tool as t

s.reset_security_policy(include_human_policy=True)
s.update_available_tools([{
    "name": "read_catalog",
    "description": "Read a synthetic catalog",
    "args": {"category": {"type": "string"}},
}])
s.update_always_allowed_tools(["local_status"])
query = "Read synthetic catalog entries in category books."
calls = [{"name": "read_catalog", "args": {"category": "books"}}]
responses = [
    '[{"name":"read_catalog","args":{"category":{"type":"string","enum":["books","music"]}}}]',
    "Yes",
    '[{"name":"read_catalog","args":{"category":{"type":"string","enum":["books"]}}}]',
]

with patch.object(t, "api_request", side_effect=responses) as request:
    s.generate_security_policy(query)
    assert s.get_current_config()["read_catalog"] == [
        (100, 0, {"category": {"type": "string", "enum": ["books", "music"]}}, 0)
    ]

    with patch.dict("os.environ", {"SECAGENT_ONLY_ALLOW_NARROW": "True"}):
        s.generate_update_security_policy(calls, "Only books are needed.", manual_check=False)

    assert s.get_current_config()["read_catalog"] == [
        (100, 0, {"category": {"type": "string", "enum": ["books"]}}, 0)
    ]
    assert s.get_current_config()["local_status"] == [(1, 0, {}, 0)]
    assert request.call_count == 3
```

## 2026-11-16

| 检查 | 实测结果 |
| --- | --- |
| 当前分支与提交 | 待手动核对。本轮未执行 Git 命令。上一轮记录的分支为 `feature/member5-policy-generation`，提交为 `b32e0a4d98daccabda3491374640ea9058895b13` |
| 改动前 | `.member5-venv/Scripts/python.exe -m pytest -q tests`，18 通过、0 失败。2 条 Python 弃用警告，1 条 pytest 缓存写入权限警告 |
| 修复前复现 | 第一条规则合法、第二条缺 `args`。旧实现覆盖了原有 `read` 规则，并留下空的 `list` 条目，保留旧策略的断言失败 |
| 改动后 | `.member5-venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests`，22 通过、0 失败。2 条原有弃用警告 |
| 默认会话兼容 | 重新运行本文件上方的固定响应脚本，通过初始生成和更新断言 |

### 改动原因

- **策略记录。** `PolicyRecord` 记录当时生效的版本和规则，也保留模型未通过验证的候选，便于解释为什么旧策略仍有效。未运行最小性检查时使用 `unverified`。
- **会话隔离。** 显式传递同一个 `PolicySession` 给工具注册、生成、更新和工具检查，可避免会话间混用任务、工具清单和规则。省略 `session` 的调用使用兼容入口。
- **校验后发布。** 完整候选先通过结构、工具及参数检查，再调用现有类型校验函数。诊断携带路径、错误码和说明，反馈给模型，最多两次修复。三次无有效候选时，保留旧规则、版本及其任务上下文。

### 接口核对

使用现有的 `DB-001.json` 验证 `task_id` 和 `must_visit.datascope` 可序列化到记录。数据范围字段用现有 `DataScope` 模型，范围来源及授权语义仍由上层调用者决定。现有类型校验器返回警告字符串，本轮由生成调用侧附加路径和错误码。拟用 `session_id` 与 `policy_version` 作为 `DecisionTrace` 的策略引用，仍待编号 3、4 确认。真实模型和框架会话接线沿用上一轮验证范围，本轮测试用固定响应。

### 被拒候选的序列化样例

下面展示回归测试里第二条规则缺少 `args` 时的记录。现有规则与版本仍然有效。

```json
{
  "session_id": "example-session",
  "policy_version": 1,
  "effective_rules": {
    "read": [[100, 0, {"key": {"const": "old"}}, 0]],
    "status": [[1, 0, {}, 0]]
  },
  "model_candidate": [{"name": "read", "args": {}}, {"name": "list"}],
  "source": "initial_generation",
  "validation_status": "rejected",
  "validation_results": [
    {"path": "$[1].args", "code": "invalid_arguments", "message": "Expected an argument object."}
  ],
  "minimality_status": "unverified",
  "task_id": null,
  "data_scopes": [],
  "model": "gpt-4o-2024-08-06"
}
```

## 2026-11-20 安全更新与框架接线

- 动态更新默认开启子集检查。明确的剩余工具调用由可信宿主传入 `create_policy_session(remaining_tool_calls=[{"name": ..., "args": ...}])` 或维护会话中的同名列表，更新候选导致其中任一调用受阻时拒绝发布并记录字段路径。TaskSpec 的资源操作尚不能自动推导成具体工具参数，该映射仍由宿主提供。
- ASB 的 `react_agent_attack.py` 已为每次 run 创建独立会话，工具检查和结果触发更新使用同一会话。沿用其现有无参数工具策略表示。
- LangChain、OpenAI Agents 的 CLI 客户端可通过可信宿主提供的 `PROGENT_POLICY_TOKEN` 转发 MCP 绑定令牌；令牌仍需代理进程内注册。
- 新增测试覆盖默认拒绝扩权、剩余调用受阻时保留原策略、合法收窄保留人工规则。原有版本冲突测试继续通过。
- 完整框架合成任务尚未通过验收。尝试安装可选依赖时发生大量版本回溯及 PyPI 超时，本轮中止安装。只完成了适配器源码接线和语法检查，不能据此认定步骤 5 完成。

## 2026-11-20 框架合成任务验收

框架依赖安装已完成，`python -m pip --python .member5-venv/Scripts/python.exe check` 返回 `No broken requirements found`。主要版本为 FastMCP 2.13.0、LangChain 1.0.2、OpenAI Agents 0.4.2、Vertex AI 1.71.1、FastAPI 0.165.0。依赖求解将 OpenAI SDK 从 3.51.0 调整为兼容的 2.54.0。本地 AgentDojo 以可编辑方式安装。

复现依赖安装（安装到项目隔离环境）：

```powershell
python -m pip --python .member5-venv/Scripts/python.exe install fastmcp==2.13.0 langchain==1.0.2 openai-agents==0.4.2 langchain-mcp-adapters cohere pyyaml deepdiff rich openapi-pydantic jsonref python-dotenv numpy pandas jsonlines langchain-chroma langchain-ollama langchain-openai fastapi vertexai==1.71.1
python -m pip --python .member5-venv/Scripts/python.exe install --no-deps -e ./agentdojo
.member5-venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_framework_synthetic_runs.py
```

新增 3 个合成任务测试均通过：

| 框架 | 实际执行路径与验证 |
| --- | --- |
| AgentDojo | 真实 AgentPipeline、InitQuery、FunctionsRuntime 和 ToolsExecutor。注册 read(key)，生成策略，执行 read(x)，回传结果后收窄为 x |
| ASB | 初始化合成 Agent 状态后执行真实 ReactAgentAttack.run()、call_tools()。固定规划/模型响应，执行合成只读工具并更新同一会话 |
| MCP | 真实 FastMCP 服务、中间件和 Client 内存传输，分别绑定两会话，完成列表注册、生成、工具调用、更新。断言两会话分别收窄为 x 和 y，退出后绑定失效 |

模型边界使用固定响应。ASB 使用合成规划、工具及已配置的 Agent 状态，不启动向量库或远程模型；MCP 使用内存传输并注入可信请求头，HTTP 部署链路尚需独立验证。此轮验收针对用户指定的 AgentDojo、ASB、MCP 三个入口。

Windows 沙箱内 asyncio 在 socketpair 初始化时阻塞，获准在沙箱外执行框架测试后通过。这是测试运行环境要求，产品代码未更换事件循环。第三方库出现弃用警告，测试无失败。

最终全套回归结果为 **317 passed, 21 warnings in 9.42s**，新增框架任务 3 项通过，警告来自 Python 与第三方弃用接口。未执行 Git 操作。

## 2026-11-20 真实模型验证（待凭据）

已新增 `real_model_validation.py`，直接调用真实 SDK，依次执行初始生成、本地只读 lookup、模型策略更新和本地只读 read。模型未产生获准的新版本时明确判为未完成更新。JSON 报告保存模型、手动提供的提交号、会话、各工具判定版本、策略记录和失败阶段。

本轮检查进程、用户及系统环境均未发现模型凭据；项目根目录、real-world-agents 和 asb 也没有 .env。实跑在 initial_generation 阶段报 OpenAIError / Missing credentials，未发出真实模型请求，未完成步骤 6。结果在 `real_model_validation_result.json`，提交字段为 UNCONFIRMED。

在已配置凭据的同一 PowerShell 中复现：

```powershell
.member5-venv/Scripts/python.exe docs/member5/real_model_validation.py --commit 手动确认的完整提交号 --output docs/member5/real_model_validation_result.json
```

读取 OPENAI_API_KEY、可选 OPENAI_BASE_URL 和 SECAGENT_POLICY_MODEL；默认模型为 gpt-4o-2024-08-06。私密凭据仅在本机环境设置，不写入报告或聊天。

## 2026-11-22 真实模型验证通过

| 项目 | 实测记录 |
| --- | --- |
| 执行方式 | 用户在本机配置凭据并执行，助手核对结果 JSON |
| 模型 | deepseek-chat |
| 用户提供的提交号 | 92e3fe03c6ec295bacd9d5c3e8e958d8e884c199 |
| 会话 | 1a4a7df4a4d54729880d16d0812440a7 |
| 初始生成 | accepted，策略版本 2 |
| lookup 调用 | 使用版本 2，返回 selected_key=x |
| 动态更新 | 版本 2 → 3，subset_checked，read_catalog_entry.key 收窄为 enum=["x"] |
| read 调用 | 使用版本 3，读取 x，返回 synthetic public value |
| 最终状态 | passed，无失败阶段 |
| 结果文件 | real_model_validation_result.json |

复现命令：

```powershell
.member5-venv/Scripts/python.exe docs/member5/real_model_validation.py --commit 92e3fe03c6ec295bacd9d5c3e8e958d8e884c199 --output docs/member5/real_model_validation_result.json
```

运行前在同一终端配置服务商凭据、兼容接口地址和 SECAGENT_POLICY_MODEL=deepseek-chat。日志不保存凭据；当前报告没有记录接口地址。提交号来自用户运行参数，本轮未用 Git 核验工作区。验证范围为真实模型策略生命周期与本地合成只读操作，严格最小性仍标为 unverified，未进行数据库或框架的真实模型端到端验证。步骤 6 的合成只读任务验收完成。

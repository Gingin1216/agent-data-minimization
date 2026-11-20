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

# 框架适配

| 项目 | 内容 |
| --- | --- |
| 文件路径 | `secagent/tool.py:718` |
| 函数名称 | `secure_tool_wrapper` |
| 调用者 | `apply_secure_tool_wrapper`，支持单函数或列表 |
| 输入参数 | `tool: Any`，实际需要可内省的函数 |
| 输出 | 保留元数据的包装函数。通过检查后返回原函数的结果 |
| 核心逻辑 | ① 读取文档和签名。② 选择支持的参数类型和默认值。③ 用 Pydantic 生成参数 Schema 并登记工具。④ 调用时绑定参数、补默认值、检查策略，再执行原函数 |
| 依赖函数 | `parse`、`inspect.signature`、`get_type_hints`、`check_type_hints`、`create_model`、`check_tool_call` |
| 当前不足 | 存在参数文档时只处理文档中的参数。文档参数名和签名漂移可导致注册异常。反复包装会重复登记 |
| 改进计划 | 以签名为准合并文档描述，并明确同名工具的注册行为 |
| 测试方法 | 覆盖部分参数有文档、错误参数名、默认值、禁止调用时原函数未执行 |

| 项目 | 内容 |
| --- | --- |
| 文件路径 | `secagent/tool.py:610` |
| 函数名称 | `SecAgentLangchainMiddleware.awrap_tool_call` |
| 调用者 | 安装可选 LangChain 依赖并注册该中间件的 LangChain Agent |
| 输入参数 | `self`，`request` 提供工具名、参数和调用 ID，`handler` 为可等待的下一层调用 |
| 输出 | 正常返回 handler 的结果，异常时返回关联同一调用 ID 的 `ToolMessage` |
| 核心逻辑 | ① 检查工具调用。② 等待 handler。③ 功能与更新开关启用时传入调用列表和 `result.content` 更新策略。④ 捕获异常并生成工具消息 |
| 依赖函数 | `check_tool_call`、`handler`、`generate_update_security_policy`、`ToolMessage` |
| 当前不足 | 更新错误与执行错误走同一返回路径，工具已经执行成功时也可能被反馈为工具错误。结构化 content 可能无法直接拼接 |
| 改进计划 | 保留已执行的工具结果，并单独报告策略更新失败。统一结果的序列化契约 |
| 测试方法 | 用异步假 handler 验证拒绝时不执行、允许时只执行一次、更新异常时仍保留成功调用结果 |

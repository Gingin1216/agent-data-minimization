# 策略更新

| 项目 | 内容 |
| --- | --- |
| 文件路径 | `secagent/tool.py:415` |
| 函数名称 | `decide_whether_to_update` |
| 调用者 | `generate_update_security_policy` |
| 输入参数 | `tool_call_param` 无注解，现有适配器通常传可 JSON 序列化的调用列表，元素含 `name` 和 `args`。依赖全局初始任务 |
| 输出 | `bool`，响应去空白并转小写后以 `yes` 开头即为真 |
| 核心逻辑 | ① 拼接工具、任务和调用参数。② 使用更新判定提示词发起请求。③ 检查前缀。④ 请求异常最多尝试六次 |
| 依赖函数 | `get_available_tools`、`json.dumps`、`api_request` |
| 当前不足 | 未初始化任务时，拼接可在重试块外抛出异常。前缀匹配会接受含糊响应 |
| 改进计划 | 显式校验任务上下文和判定响应 |
| 测试方法 | 覆盖未初始化、合法 Yes/No、含糊前缀与模型异常 |

| 项目 | 内容 |
| --- | --- |
| 文件路径 | `secagent/tool.py:435` |
| 函数名称 | `generate_update_security_policy` |
| 调用者 | LangChain 工具中间件、OpenAI Agents SDK 事件循环、AgentDojo 工具执行器、Progent MCP 调用中间件 |
| 输入参数 | `tool_call_param` 无注解且须可 JSON 序列化，`tool_call_result` 无注解且直接拼接为字符串，`manual_check` 默认 `True` |
| 输出 | 返回 `None`。应用后替换全局生成规则，更新可能因关闭生成、判定为否、返回空候选、人工拒绝或子集校验失败而跳过 |
| 核心逻辑 | ① 检查开关并请求更新判定。② 收集工具、初始任务、调用参数、结果和当前生成规则。③ 请求候选并解析 JSON。④ 可选人工确认。⑤ 启用 `SECAGENT_ONLY_ALLOW_NARROW` 时构造临时策略并验证子集。⑥ 写回生成规则。异常最多尝试六次，最终异常可按配置忽略 |
| 依赖函数 | `decide_whether_to_update`、`get_generated_policy`、`get_SYS_PROMPT_2`、`api_request`、`extract_json`、`security_policy_subset_check`、`delete_generated_policy` |
| 当前不足 | 子集校验默认关闭。工具结果全文进入提示词和日志。直接拼接字符串对结构化结果兼容性有限。更新判定和上下文拼接处于更新重试块之外 |
| 改进计划 | 规范化工具结果并控制发送与日志内容，提交前校验完整候选并原子替换，显式返回更新状态 |
| 测试方法 | 固定模型判定和候选，验证跳过、成功收窄、拒绝扩权及拒绝时保留旧规则。用结构化工具结果检验规范化逻辑 |

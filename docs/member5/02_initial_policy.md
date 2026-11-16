# 初始策略

| 项目 | 内容 |
| --- | --- |
| 文件路径 | `secagent/tool.py:375` |
| 函数名称 | `generate_security_policy` |
| 调用者 | `secagent/progent_proxy.py` 的 `proxy_request` 和 `ListingFilterMiddleware.on_list_tools`，以及 AgentDojo、LangChain 和 OpenAI Agents SDK 入口 |
| 输入参数 | `query: str`，`manual_check` 默认 `False`。工具列表通过 `get_available_tools()` 从模块全局状态读取，每项含 `name`、`description`、`args` |
| 输出 | 返回 `None`。成功时写入全局 `security_policy`，结构为工具名到规则列表的映射，模型规则为 `(100, 0, args, 0)` |
| 核心逻辑 | ① 保存原始任务，检查生成开关。② 序列化工具定义并拼接任务文本。③ 请求模型并解析 JSON，Claude 要求 JSON 代码块。④ 可选终端确认。⑤ 删除旧生成规则，逐项追加新的允许规则。⑥ 捕获异常后提高温度，最多尝试六次 |
| 依赖函数 | `get_available_tools`、`get_SYS_PROMPT`、`api_request`、`extract_json`、`delete_generated_policy` |
| 当前不足 | 模型候选未经完整结构与工具参数检查。解析成功但候选条目缺字段时，旧生成规则可能已删除，新规则也可能只写入一部分。数据库 Schema 和 TaskSpec 均未进入此函数的输入 |
| 改进计划 | 先构造并校验完整候选，再一次性替换。若接入数据库任务，为候选加入明确的 Schema 和任务约束 |
| 测试方法 | 注册虚构只读工具并固定模型输出，检查规则写入和人工规则保留。让第二个候选条目缺字段，断言失败后原规则完整保留 |

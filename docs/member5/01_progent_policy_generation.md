# Progent 策略生成调用链

基线提交为 `b32e0a4d98daccabda3491374640ea9058895b13`。适配器先注册 `Tool` 定义，初始生成把定义和用户任务交给模型，运行时检查工具调用，部分适配器再用执行结果触发更新。

| 项目 | 内容 |
| --- | --- |
| 文件路径 | `secagent/tool.py:167` |
| 函数名称 | `api_request` |
| 调用者 | `generate_security_policy`、`decide_whether_to_update`、`generate_update_security_policy` |
| 输入参数 | `sys_prompt`、`user_prompt` 实际为字符串，`temperature` 默认 `0.0`。从模块配置读取 `policy_model` |
| 输出 | 实际返回模型响应文本，源代码标注为 `None` |
| 核心逻辑 | ① 按模型名选择 SDK 和消息格式。② 发出生成请求。③ 从提供商响应读取文本，供上层解析 |
| 依赖函数 | `Anthropic.messages.create`、Vertex AI `generate_content`、`OpenAI.chat.completions.create` |
| 当前不足 | 返回注解与实际不符。上层对认证失败和模型输出错误使用同一种重试逻辑 |
| 改进计划 | 修正返回注解，区分终止错误与可重试错误 |
| 测试方法 | 替换 SDK 客户端，验证消息映射和返回类型，通过认证失败与临时服务错误验证重试次数 |

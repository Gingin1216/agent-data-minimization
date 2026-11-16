# Progent 基线测试

| 项目 | 记录内容 |
| --- | --- |
| 日期 | 2026-11-15（Asia/Shanghai） |
| Python | CPython 3.14.3 |
| 操作系统 | Windows NT 10.0.26200.0 |
| 原始代码提交 | `b32e0a4d98daccabda3491374640ea9058895b13`，分支 `feature/member5-policy-generation` 开始记录前的 HEAD |
| 依赖安装 | 成功。创建项目内隔离环境后，使用宿主 pip 的 `--python` 安装可编辑包和 pytest。沙箱内安装曾因临时目录权限失败，获批后重跑成功 |
| secagent 导入 | 成功，加载当前工作区的 `secagent/__init__.py` |
| 单元测试 | 18 通过 / 0 失败，2 条弃用警告。范围为仓库根目录的 `tests/` |
| 策略生成 | 真实模型失败，SDK 缺少凭据。固定响应冒烟测试成功，断言生成规则写入 |
| 策略更新 | 真实模型失败，更新判定阶段缺少凭据。固定响应冒烟测试成功，断言规则收窄并保留人工规则 |
| 异常 | 下方保留安装错误、测试警告和两条完整回溯 |

## 复现命令

```powershell
python -m venv --without-pip .member5-venv
python -m pip --python .member5-venv/Scripts/python.exe --disable-pip-version-check --no-color install -e . pytest
.member5-venv/Scripts/python.exe -c "import secagent; print(secagent.__file__)"
.member5-venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests
```

主要依赖实装版本为 secagent 0.1.0、pytest 9.2.0、openai 3.51.0、anthropic 2.8.0、pydantic 2.14.3、jsonschema 4.26.0、docstring-parser 0.18.0、sqlglot 30.31.0、z3-solver 5.4.1.0。默认策略模型为 `gpt-4o-2024-08-06`。

更新的真实 SDK 检查在独立进程设置了 `init_user_query`，以便先验证更新判定的模型调用。此步骤在 SDK 客户端初始化时中断，未请求候选更新。固定响应冒烟测试仅替换 `secagent.tool.api_request`，解析、规则写入和子集校验均由原始代码执行。见[实验日志](experiment_log.md)。

## 安装异常

首次通过默认 `venv` 初始化 pip 失败，重建时使用了 `--without-pip`。

```text
Actual environment location may have moved due to redirects, links or junctions.
  Requested location: "C:\Users\LIXIAN~1\AppData\Local\Temp\progent-member5-a118063a2687499c8a62021136c9ec78\Scripts\python.exe"
  Actual location:    "C:\Users\Lixiangdan\AppData\Local\Temp\progent-member5-a118063a2687499c8a62021136c9ec78\Scripts\python.exe"
Error: Command '['C:\\Users\\Lixiangdan\\AppData\\Local\\Temp\\progent-member5-a118063a2687499c8a62021136c9ec78\\Scripts\\python.exe', '-m', 'ensurepip', '--upgrade', '--default-pip']' returned non-zero exit status 1.
C:\Users\LIXIAN~1\AppData\Local\Temp\progent-member5-a118063a2687499c8a62021136c9ec78\Scripts\python.exe: No module named pip
```

```text
Obtaining file:///D:/AllProject/agent-data-minimization
ERROR: Could not install packages due to an OSError: [Errno 13] Permission denied: 'C:\\Users\\Lixiangdan\\AppData\\Local\\Temp\\pip-build-tracker-m1d6g_gg\\a0f55ce1df6b02c12d890889450ff427fb902303ed34a71c58b9de85'
Check the permissions.

WARNING: Failed to remove contents in a temporary directory 'C:\Users\Lixiangdan\AppData\Local\Temp\pip-build-tracker-m1d6g_gg'.
You can safely remove it manually.
WARNING: Failed to remove contents in a temporary directory 'C:\Users\Lixiangdan\AppData\Local\Temp\pip-ephem-wheel-cache-0qq32edx'.
You can safely remove it manually.
```

## pytest 输出

```text
..................                                                       [100%]
============================== warnings summary ===============================
secagent\role_analyzer.py:6
  D:\AllProject\agent-data-minimization\secagent\role_analyzer.py:6: DeprecationWarning: module 'sre_constants' is deprecated
    import sre_constants

secagent\role_analyzer.py:7
  D:\AllProject\agent-data-minimization\secagent\role_analyzer.py:7: DeprecationWarning: module 'sre_parse' is deprecated
    import sre_parse

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
18 passed, 2 warnings in 0.30s
```

## 真实模型生成报错

```text
D:\AllProject\agent-data-minimization\secagent\policy_analysis.py:468: SyntaxWarning: "\." is an invalid escape sequence. Such sequences will not work in the future. Did you mean "\\."? A raw string is also an option.
  (1, 0, {"recipient": {"type": "string", "pattern": "[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"}}, 0),
Policy Model: gpt-4o-2024-08-06
Traceback (most recent call last):
  File "<string>", line 1, in <module>
    import secagent as s; import secagent.tool as t; s.generate_security_policy("Read a synthetic catalog.")
                                                     ~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "D:\AllProject\agent-data-minimization\secagent\tool.py", line 412, in generate_security_policy
    raise e
  File "D:\AllProject\agent-data-minimization\secagent\tool.py", line 386, in generate_security_policy
    res = api_request(get_SYS_PROMPT(), content, temperature)
  File "D:\AllProject\agent-data-minimization\secagent\tool.py", line 205, in api_request
    client = OpenAI()
  File "D:\AllProject\agent-data-minimization\.member5-venv\Lib\site-packages\openai\_client.py", line 323, in __init__
    return sync__init__(
        _runtime_cast(_RuntimeAny, super()).__thisclass__,
    ...<17 lines>...
        _enforce_credentials=_enforce_credentials,
    )
  File "D:\AllProject\agent-data-minimization\.member5-venv\Lib\site-packages\openai\lib\_client_auth.py", line 199, in sync__init__
    raise _sdk_client.OpenAIError(
        "Missing credentials. Please pass an `api_key`, `workload_identity`, `admin_api_key`, or set the `OPENAI_API_KEY` or `OPENAI_ADMIN_KEY` environment variable."
    )
openai.OpenAIError: Missing credentials. Please pass an `api_key`, `workload_identity`, `admin_api_key`, or set the `OPENAI_API_KEY` or `OPENAI_ADMIN_KEY` environment variable.
```

## 真实模型更新报错

```text
Policy Model: gpt-4o-2024-08-06
Traceback (most recent call last):
  File "<string>", line 1, in <module>
    import secagent as s; import secagent.tool as t; t.init_user_query = "Read a synthetic catalog."; s.generate_update_security_policy([], "Synthetic result.", manual_check=False)
                                                                                                      ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "D:\AllProject\agent-data-minimization\secagent\tool.py", line 439, in generate_update_security_policy
    if not decide_whether_to_update(tool_call_param):
           ~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^
  File "D:\AllProject\agent-data-minimization\secagent\tool.py", line 432, in decide_whether_to_update
    raise e
  File "D:\AllProject\agent-data-minimization\secagent\tool.py", line 423, in decide_whether_to_update
    res = api_request(SYS_PROMPT_UPDATE, content, temperature)
  File "D:\AllProject\agent-data-minimization\secagent\tool.py", line 205, in api_request
    client = OpenAI()
  File "D:\AllProject\agent-data-minimization\.member5-venv\Lib\site-packages\openai\_client.py", line 323, in __init__
    return sync__init__(
        _runtime_cast(_RuntimeAny, super()).__thisclass__,
    ...<17 lines>...
        _enforce_credentials=_enforce_credentials,
    )
  File "D:\AllProject\agent-data-minimization\.member5-venv\Lib\site-packages\openai\lib\_client_auth.py", line 199, in sync__init__
    raise _sdk_client.OpenAIError(
        "Missing credentials. Please pass an `api_key`, `workload_identity`, `admin_api_key`, or set the `OPENAI_API_KEY` or `OPENAI_ADMIN_KEY` environment variable."
    )
openai.OpenAIError: Missing credentials. Please pass an `api_key`, `workload_identity`, `admin_api_key`, or set the `OPENAI_API_KEY` or `OPENAI_ADMIN_KEY` environment variable.
```

# Banking Agent-only实验运行器

在仓库根目录安装agentdojo和根项目依赖，使用Linux或WSL。脚本从自身位置定位仓库；无需个人备份目录。预算模块budget.py正式提供Ledger、BudgetStop与原子保存。

离线检查，无需密钥：
python experiments/unified/run.py --help
python experiments/unified/run.py agent-attack --prepare
python experiments/unified/run.py agent-normal --prepare --tasks user_task_0

真实运行需另行授权，并由本地终端安全设置BAILIAN_AGENT_API_KEY。请勿写进源码或Git。
bash experiments/unified/run.sh agent-normal --tasks user_task_0 --output runs/NEW
模型固定qwen3.8-flash，百炼兼容接口，SDK与Agent请求重试关闭，Policy关闭。预算在HTTP实际发出前记账，断连保留pending并停止续跑。原始AgentDojo评分不修改。该入口独立于evaluation静态范围评测。

四模式中agent-normal和agent-attack已实现，full-normal和full-attack明确报错，尚未实现。默认攻击仅4组Pilot；新增16配对使用--pairs显式传入。保存原生trace、tools、exposure、逐例JSON和预算账本，仅留本地；共享报告采用指标白名单。

资源上限默认单任务32、批次128请求，输入300000、输出24576Token，单次输出最多1024。--resume要求任务与预算一致，已完成案例跳过，pending或未完成目录停止。HTTP错误与未知usage停止，不把预算中断计为Utility失败。无隐式充值或模型回退。

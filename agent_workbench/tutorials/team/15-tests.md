# 15 · 测试、评测和证据的层次

源码入口：tests/test_team.py、其他历史 tests、.github/workflows/ci.yml。

v2 测试覆盖用户可观察行为：无登录本机边界、凭据不落数据库、任务状态、审批、路径越界、合入冲突、计划原子性、项目成员范围、群聊上下文、失败恢复、预算并发以及协议适配。测试名称对应场景，可以先读断言再回源码理解原因。

ScriptModel 返回预先安排的工具调用，用于确定性测试“模型选择写文件后系统怎么做”；httpx.MockTransport 检查 HTTP payload 与响应解析。它们不证明任何真实模型的推理能力。MemoryCredentials 用于避免测试污染你的系统凭据。

真实本地集成测试会启动子进程、运行 MCP stdio 和 HTTP 服务、执行插件脚本、用 Chromium 点击按钮并检查截图。这些确实运行了工具，但服务是测试夹具，不代表验证过所有第三方 MCP 服务。

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_team.py -q
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check agent_workbench tests
node --check agent_workbench/team_web/app.js
```

完整测试还保留旧 v1 回归。python -m agent_workbench eval / app-eval 分别运行旧版知识检索与应用合成验收，不能把它们的通过数当成 v2 模型任务成功率。

真正的产品评测应固定真实任务集及验收脚本，记录模型版本、提示、工具权限、预算、调用费用、耗时、失败原因和人工介入次数。同一任务比较不同编排方式，避免只挑成功演示。API Key 和真实敏感资料不写评测文件。

CI 使用锁定且带哈希依赖，在 Windows/Ubuntu 与 Python 3.11/3.12 运行。矩阵通过只说明这些受测环境；不能外推到任意操作系统、任意模型和超大项目。真实供应商、镜像构建、部署只有实际运行成功才能报告。

自检：为什么要测试“预算同时不够”的并发场景，而不只测试单个加法？答案：核心风险是两个线程都先看到剩余额度，再分别越限；只有并发行为才能验证事务边界。

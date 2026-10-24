# Agent Workbench · 本地多模型工作群

一个人管理一支 AI 软件团队：在同一个工作群中安排任务，让不同模型分工、交接、验证和交付。**本地部署，无需登录，自带模型接口。**

当前版本 **2.0**。默认入口已从旧版知识工单系统切换为本地软件协作台。可以先跑离线规则演示，随后在成员页面配置自己的模型；实际交付与验证范围见 [DELIVERY](docs/DELIVERY.md)。

## 启动

需要 Python 3.11+。Windows 双击 [启动项目.bat](启动项目.bat)，或运行：

```powershell
.\scripts\start.ps1
```

打开 [本地工作台](http://127.0.0.1:8765)。首次安装依赖需要网络；已有 `.env` 不覆盖。浏览器检查工具另需安装 Chromium：

```powershell
.\scripts\install-browser.ps1
```

macOS / Linux：`sh scripts/start.sh`，然后运行 `.venv/bin/python -m playwright install --with-deps chromium` 安装浏览器运行环境。

## 先体验一条完整流程

1. 打开默认项目，在输入框选择「自动规划」，发送“创建一个示例网页并交接”。
2. 打开规划任务，检查计划，点击「采用计划，创建草稿任务」。
3. 点击「开始草稿任务」。演示开发员生成静态页面，演示审查员在依赖完成后写交接文档。
4. 在任务详情查看文件、差异和成果预览，再点击「检查后合入原项目」。
5. 到项目设置查看本地目录，实际文件就在其中。演示使用固定规则，不是 LLM 推理，也不产生模型调用费用。

## 接入你的模型

在「团队成员」添加名称、职责、Base URL、具体模型 ID、能力档位和工具权限。接口需支持 Chat Completions 的函数工具调用。可创建多个成员，使用相同或不同供应商模型。

- API Key 可在界面填写，保存在操作系统凭据存储；数据库只保存成员配置。
- 也可以在本地 `.env` 写 `DEEPSEEK_API_KEY=...` 等变量，在成员表单的「或使用 .env 变量名」填写变量名。修改环境文件后重启。
- 环境变量引用优先于界面密钥。无桌面凭据服务的 Linux/容器优先使用 `.env`。
- 模型 ID 和价格以你供应商账号当前可用内容为准；不预设“某品牌一定最强”。真实供应商实连须由配置后的验证确认。

项目设置可选择入群成员。手动创建任务、在聊天中选成员、`@成员名称`、按档位自动选择、让模型生成待确认的分工计划，均可使用。普通消息保留在群里，活跃任务在下一次模型调用时获得近期项目讨论。

## 已提供的能力

| 模块 | 实际行为 |
| --- | --- |
| 群聊与任务 | SSE 实时事件、持久化历史、手动分工、模型规划、依赖等待 |
| 多模型协作 | 单成员串行、不同成员最多 3 个任务并发；完成后交接报告及文件 |
| 费用管理 | 按配置档位与单价路由，调用前原子预留预算，保留每次调用账单 |
| 文件开发 | 独立 base/work 副本，读取/搜索/写入/精确编辑，展示差异与冲突检查合入 |
| 验证工具 | 每次确认后执行命令；静态页面浏览器点击/填写、页面错误与截图 |
| Skills | 导入包含 YAML 元数据的 SKILL.md 目录，按需读取正文及资源 |
| MCP | 官方 Python SDK，stdio 与 Streamable HTTP，发现工具、校验参数、逐次确认调用 |
| 插件 | 本项目声明式 plugin.json 工具清单，固定 argv、JSON Schema、启停及检查 |
| 恢复 | 停止、从原项目重试、保留未完成文件继续、重启标记中断，不自动重放外部操作 |
| 教程 | 内置 18 章中文课程，覆盖运行流程、各模块、取舍、调试与扩展 |

多模型并不自动降低费用。当前自动路由是按用户配置档位与输入单价选择；是否节省，要用相同任务、相同验收条件比较总调用账单、成功率和返工。金额按用户填写价格估算，未计缓存折扣、外部工具费用等。

## 学习与代码入口

在页面左侧打开「学习手册」，或从 [第一章](agent_workbench/tutorials/team/00-start.md) 开始。[源码导航](docs/ARCHITECTURE.md)和[开源参考](docs/SOURCES.md)解释具体实现。

```text
agent_workbench/team/
  contracts.py   HTTP 与模型工具输入契约
  api.py         本机 API、SSE、预览、成员和扩展管理
  runtime.py     调度、模型循环、审批、规划、交接与恢复
  db.py          SQLite、事件、预算账本、启动恢复
  workspace.py   文件边界、工作副本、差异合入、命令和浏览器
  model.py       Chat Completions 适配、用量解析
  credentials.py 系统凭据与环境变量引用
  extensions.py  Skill / MCP / 插件
agent_workbench/team_web/       原生 HTML/CSS/JavaScript 界面
agent_workbench/tutorials/team/ 18 章当前版本教程
agent_workbench/team_assets/    自带技能与示例插件
examples/mcp_server.py          可运行的 MCP 示例
tests/test_team.py             v2 行为和真实本地工具测试
```

旧版企业知识工单代码与原教程保留，用于历史学习和回归；不参与 v2 默认页面，不需要旧版账号。旧数据库不删除，v2 单独使用 `var/team/team.db`。

## 验证与运行边界

```powershell
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-dev.lock
.\scripts\install-browser.ps1
.\scripts\test.ps1
node --check agent_workbench/team_web/app.js
```

`eval` / `app-eval` 保留旧版合成回归；v2 行为验收在 `tests/test_team.py`。安装依赖依据 `uv.lock` 和带哈希 requirements。GitHub Actions 在 Windows / Ubuntu、Python 3.11 / 3.12 执行检查。

默认只监听本机，单进程运行；不要给 Uvicorn 增加多个 worker。工作副本是文件隔离，不是操作系统沙箱。已确认的命令、插件及 MCP 服务具有其进程实际权限。内置预览与浏览器检查针对静态网页；后端应用由命令工具验证或按产物启动说明在本机启动。

提供 Dockerfile 与 Compose；Compose 端口绑定 `127.0.0.1`。容器只能访问挂载进去的项目，不能直接使用 Windows 主机路径；当前是否完成镜像验证以交付记录为准。

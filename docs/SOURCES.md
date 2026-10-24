# 开源参考与依赖来源

查阅日期：2026-10-21 至 2026-10-22。以下是设计参考和官方协议来源，不代表项目继承了这些上游的全部能力。

| 来源 | 借鉴/使用 | 本项目对应实现 |
| --- | --- | --- |
| [DeepSeek Harness 架构](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/architecture.md) | 将模型、工具、会话和执行循环分层 | team/model.py、runtime.py、extensions.py |
| [DSH Agent Team](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/agent-team.md) | 成员、任务依赖、消息与交接的组织方式 | 项目成员、DAG、持久化群事件、依赖成果 |
| [DSH Plugin Manager](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/boot/plugin-manager/README.md) | 区分配置、启用和实际加载 | 扩展启停、检查连接、固定清单 |
| [AutoGen SelectorGroupChat](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/selector-group-chat.html) | 群聊中选择下一位成员的概念 | 人工指定、自动路由、模型生成计划；并发由本项目调度器实现 |
| [Deep Agents](https://github.com/langchain-ai/deepagents) | 按需读取技能与文件上下文 | 技能目录元数据、read_skill、有限上下文 |
| [Agent Skills 规范](https://agentskills.io/specification) | SKILL.md 的 YAML 元数据与正文、资源目录 | 导入并按需读取；不将 allowed-tools 当作宿主授权 |
| [官方 MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) | 实际依赖 mcp==2.3.0，协议与传输 | stdio / Streamable HTTP、工具发现和调用 |
| [MCP 架构说明](https://modelcontextprotocol.io/docs/learn/architecture) | 客户端、服务端与能力发现 | Extensions 与服务端保持协议边界 |
| [OpenAI Function Calling](https://developers.openai.com/api/docs/guides/function-calling) | 函数调用与工具结果消息协议 | 兼容 Chat Completions 适配器；不宣称支持所有厂商原生协议 |
| [Playwright Python](https://playwright.dev/python/docs/intro) | 实际浏览器驱动依赖 | 静态页面动作、错误收集与截图 |

本轮没有将 DSH、AutoGen 或 Deep Agents 的源码复制入仓库，没有改名伪装成原创，也没有将它们同时作为运行时依赖。引用的是设计概念，项目具体实现和示例由本仓库编写。直接第三方依赖通过 Python 包安装，上游版权和许可仍由各包自己的 LICENSE / 元数据规定。将来复制上游实现时，应记录源文件、提交版本、许可和修改说明；现有链接指向可变上游分支，不是逐行代码溯源声明。

依赖选择：mcp 负责协议互通，成本是额外异步/HTTP 依赖；keyring 避免明文持久化密钥，成本是系统凭据服务依赖；Playwright 提供真实浏览器验证，成本是较大的浏览器下载；jsonschema 校验第三方工具参数；PyYAML 读取技能元数据。没有为了展示多 Agent 而增加默认后台模型调用。

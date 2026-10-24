# 11 · MCP：连接外部工具

源码入口：team/extensions.py 的 validate_extension、mcp_operation；examples/mcp_server.py。

MCP 将工具提供方和 Agent 宿主分开。服务端声明工具名称、说明、参数 Schema；客户端先发现工具，再提交调用。本项目使用官方 mcp==2.3.0 Python SDK，协议协商由 SDK 负责，不手写 JSON-RPC 状态机。

支持两种传输。stdio 启动一个本地子进程，通过标准输入/输出通信；Streamable HTTP 连接配置的 URL。连接检查会真实启动程序或访问服务，所以界面明确说明这个动作。启用不是检查成功；检查成功也不是授权模型任意调用。

使用仓库示例：在能力中心新增 MCP，启用，配置如下，并将路径改为实际位置。

```json
{
  "transport": "stdio",
  "command": "D:/Aaa-Projects/Multi-Agent/.venv/Scripts/python.exe",
  "args": ["D:/Aaa-Projects/Multi-Agent/examples/mcp_server.py"]
}
```

点击检查连接后会发现 estimate_effort；它只是固定公式示例，不是工时预测模型。可以让真实成员调用它，页面会等待你确认传入的 files/tests 参数。

HTTP 例子：{"transport":"http","url":"http://127.0.0.1:8000/mcp","bearer_env":"MCP_ACCESS_TOKEN"}。远程地址要求 HTTPS，本机可 HTTP。stdio 的 env_refs 使用变量名映射，例如 {"SERVICE_TOKEN":"MY_SERVICE_TOKEN"}，由宿主环境读取，配置不保存明文 token。

调用前先确认扩展启用、工具已发现、参数符合 JSON Schema，然后进入 SDK。每次外部操作仍需负责人确认。当前每次调用重新建立客户端会话，并设整体超时；适合独立工具调用，未实现跨任务持久 MCP 会话、OAuth 登录流程、资源订阅或远端任务管理。

排查顺序：使用的 Python 是否装了 mcp；脚本绝对路径是否正确；stdio 日志是否误写到 stdout；HTTP URL 是否包含服务所需 /mcp 路径；环境变量是否在启动前加载。本机 HTTP 直接连接，避免系统代理误拦截 localhost。

自检：为什么插件发现工具后仍要参数校验？答案：模型生成的是不可信输入，发现过程给出的 Schema 应用于每次实际调用，不能只用于提示模型。

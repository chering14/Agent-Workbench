# 12 · 声明式插件与扩展管理

源码入口：extensions.load_plugin、Extensions.inspect、Extensions.call；team_assets/plugins/project-inspector。

本项目插件是 Workbench v1 JSON 清单，提供一组可复用命令工具。这样可以把重复脚本的参数和命令显式登记。它不等同于 DSH、VS Code 或浏览器插件格式，第三方插件需要适配。

```json
{
  "schema_version": 1,
  "tools": [{
    "name": "count_files",
    "description": "统计工作副本中的文件",
    "input_schema": {"type":"object","properties":{},"additionalProperties":false},
    "argv": ["python", "{plugin_dir}/inspect_project.py", "{workspace}"],
    "timeout": 30
  }]
}
```

能力中心添加 plugin 类型，path 指向清单绝对路径。导入校验 schema_version、工具名称唯一性、argv 和参数 Schema；检查连接重新读清单，固定当前配置快照，并展示工具目录。插件检查不等于已经执行每个工具。

实际调用按已登记清单展开占位符。{workspace} 是当前任务 work 目录，{plugin_dir} 是插件所在目录，{arg:name} 将参数作为一个 argv 元素传入。不会把模型文字拼成任意 shell 命令。工具仍由 run_command 执行，受超时、输出边界和当次确认约束。

配置快照固定的是清单，不是整个磁盘脚本和依赖。若你修改 inspect_project.py，运行时会执行修改后的文件，因此只导入你信任的本地插件；本版没有插件签名、自动升级、在线安装和依赖沙箱。

插件与 MCP 的选择：本地小脚本用清单更简单；已有符合 MCP 的服务直接接 MCP；一套做事流程用 Skill。三者通过统一扩展列表管理，但能力和生命周期不同，不应为了统一 UI 隐瞒差异。

新插件要附清晰输入、输出、错误条件、权限与测试。比如统计工具应返回实际计数，而不是只返回“执行成功”。维护者能通过 tests/test_team.py 的真实插件测试看到从清单到子进程的完整链路。

自检：更新清单后为什么要重新检查？答案：让工具目录和参数 Schema 与新配置一致，并使旧的待确认调用因版本变化失效，避免审批的参数与实际配置不匹配。

# 13 · 浏览器、测试与可运行成果

源码入口：workspace.browser_check、run_command；api.preview；team_web/app.js 的 showPreview。

软件交付至少需要文件、启动方式、验收条件和验证证据。只看模型报告无法确认软件运行。项目提供命令工具执行测试，也提供浏览器工具检查静态网页，两者都必须先确认。

browser_check 在工作副本临时启动 127.0.0.1 静态服务，打开独立 Chromium，按 role/name 精确定位做 click 或 fill，收集 pageerror、页面标题/正文并截图。结束后关闭浏览器和临时服务。

```json
{
  "path": "index.html",
  "actions": [{"action":"click","role":"button","name":"添加"}]
}
```

第一次使用需安装 Playwright 浏览器：Windows 运行 scripts/install-browser.ps1；Linux/macOS 用项目解释器执行 playwright install --with-deps chromium。Python 包安装成功不等于浏览器文件已经下载。下载失败应修复网络后重试，不把检查标记为通过。

浏览器工具当前面向静态页面。React/Vite 等项目可以先构建为静态目录再检查，但任务副本过滤 build/dist 等目录的策略需理解；后端接口、热更新服务器和数据库程序应按项目实际方式用命令验证或本机手动启动。当前没有常驻开发服务管理器。

页面里的“成果预览”是另一个功能：通过隔离 iframe 展示任务 HTML，CSP 禁止网络连接，不提供工作台写令牌。预览便于人工查看；它没有替代 browser_check 的交互日志和截图，也不构成全面安全沙箱。

验证报告应写具体命令、退出码、覆盖范围和未覆盖部分。例如“python -m pytest -q，8 个用例通过；未测试真实支付接口”。测试跑失败时保留输出，让模型修复后重新执行。不能修改断言为总是成功，也不能把未执行检查写成已通过。

自检：截图看起来正确，为什么还需要断言？答案：截图只能显示某个时刻的外观。计算、错误处理、交接、预算和权限等行为需要可重复的测试条件验证。

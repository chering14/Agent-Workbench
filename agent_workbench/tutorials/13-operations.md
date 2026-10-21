# 13 · 配置、部署与排障

代码入口：`config.py`、`.env.example`、`scripts/`、`Dockerfile`、`compose.yaml`。依赖版本记录在 pyproject.toml、uv.lock 和导出的锁定 requirements 中。

## 环境与依赖

Windows 启动脚本自动建立虚拟环境。手动安装：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements.lock
.\.venv\Scripts\python.exe -m agent_workbench serve
```

开发验证使用 `requirements-dev.lock`，包含 pytest 和 Ruff。修改依赖后重新生成 lock 与导出文件，不应只手改一份清单。

## 真实模型检查

配置 .env 后先执行：

```powershell
.\.venv\Scripts\python.exe -m agent_workbench check-model
```

该命令会真实调用已配置模型，可能产生费用；它检查工具请求协议，不替代业务评测。真实模式配置缺失会明确报错，不会悄悄降级为规则演示。

## 容器部署

```powershell
docker compose up --build -d
docker compose logs -f
docker compose down
```

Compose 默认只绑定本机 8765 端口，持久化数据放在 workbench-data 命名卷；本地启动使用 var 目录。镜像使用非 root 用户、健康检查和固定依赖。当前应用只能以一个进程运行，扩展前必须重新设计后台任务所有权与数据库。

对外提供服务时，在前方放 HTTPS 反向代理，开启 secure Cookie，关闭 SSE 缓冲，设置请求大小和请求速率限制，替换演示账号。容器配置可检查不代表镜像已经在当前机器实际构建成功，应分别记录证据。

## 常见错误

| 现象 | 检查 |
| --- | --- |
| 401 | 登录是否有效；模型 401 还需检查提供商 Key 与端点 |
| 403 | CSRF、来源、管理员身份与资源权限 |
| 409 | 会话已有任务，或草稿已拒绝/尚未运行完成 |
| model_http_400 | 模型是否支持工具、required 与 strict 参数 |
| model_timeout_or_network_error | 网络、代理、端点和超时设置 |
| model_call_budget_exceeded | 提示是否循环，工具结果是否满足模型需要 |
| 无资料 | 当前身份、资料角色、查询用词及分段 |
| SSE 无更新 | 登录状态、代理缓冲与服务进程是否存活 |

## 运维边界

事件记录阶段与来源 ID，不保存完整模型提示。会话和引用本身保存在数据库，仍可能包含敏感数据。正式使用需制定保留、删除、备份和访问策略。服务重启后未完成任务标记失败，已完成工单与草稿保留。

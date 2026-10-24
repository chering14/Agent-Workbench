# 16 · 配置、部署、备份与排障

源码入口：__main__.py、scripts、Dockerfile、compose.yaml、team/credentials.py。

默认 serve 启动 v2，APP_HOST 默认 127.0.0.1，APP_PORT 默认 8765，TEAM_DATA_DIR 默认 var/team。首次数据库为空且 LLM_MODEL 非空时导入一个真实模型成员；数据库已有成员后，修改模型 ID/Base URL 要通过成员页面，环境文件不会每次覆盖用户配置。

环境变量引用通过 key_env 读取，并优先于系统凭据。Linux 无 Secret Service 或容器里 keyring 不可用时，用 .env 引用即可。不要将 .env、系统凭据、team.db、任务副本或真实调用日志提交 Git。

requirements.lock 是运行依赖，requirements-dev.lock 包含 pytest/ruff；两者由 uv.lock 导出并带哈希。更新依赖时同步 pyproject.toml、uv.lock 和导出文件，重新安装验证。只检查“import 成功”不能证明所有版本仍匹配。

单进程执行：不要 uvicorn --workers 4。线程池负责有限并发。结束应用后旧活动任务标记中断，重开从任务详情选择继续或重新尝试。正在等待模型 HTTP 的任务取消可能要等当前网络请求返回，已发生费用不能撤销。

Docker 提供可选容器方案，Compose 只把端口绑定在宿主 127.0.0.1。容器需 --container 才允许内部绑定 0.0.0.0；网页仍只接受本机 Host。要处理宿主项目，必须显式挂载目录，并填写容器内路径。系统凭据一般用环境变量替代。镜像是否实际验证以 DELIVERY 为准。

备份：先停止服务，复制 TEAM_DATA_DIR 与项目原目录；.env 单独妥善保留，系统凭据按系统方式备份。不要只复制 work 而忽略 base 和数据库，否则无法正确检查合入冲突。

常见问题：8765 已占用可设 APP_PORT；模型 HTTP 401/403 检查密钥与权限，404 检查 Base URL/模型 ID；预算不足检查预留和未知用量；MCP 子进程无法启动用绝对解释器路径；浏览器不存在先安装；合入冲突先审查原项目新改动，不强制覆盖。

自检：能不能把监听地址改成 0.0.0.0 就当团队在线版本？答案：不能。当前没有多用户身份、远程授权和分布式任务领取机制，公网部署需要另行设计和验证。

# 项目协作约定

## 目标与状态

用户要求先完成完整 Agent 项目及覆盖全项目教程，后续主要做功能调整。
用户能在 AI 协助下完成项目，希望直接学习成品；模型凭据由用户随后在本地 .env 配置。
业务是企业知识与工单协作，1.0 实现及验证范围见 docs/DELIVERY.md。

## 开发

- 中文解释具体代码、数据流、取舍与验证，练习不作为继续开发的前置条件。
- 变更同步更新契约、规则演示、测试和 agent_workbench/tutorials/ 教程。
- 重要设计写 docs/decisions/。区分已实现、已验证和待配置。
- 规则不称为 LLM 推理，Mock 不称为真实供应商调用，合成评测不称为真实业务准确率。
- 不承诺“完美”、录用或未经测量的规模能力。
- Python 3.11+、FastAPI、SQLite、原生 Web，默认单进程，后台任务不能直接多 worker 扩展。
- 模型、Agent、检索、存储、契约和界面分离。
- 身份来自会话，不接受模型或问题传入角色/租户，资料在进入模型前过滤。
- 工具白名单与参数校验；副作用需要明确意图、确认、幂等和审计。
- 凭据从环境读取，不写聊天、日志、Git 和评测。
- 数据库变更需要迁移，不删除用户数据掩盖问题。
- 增加 Agent 或依赖需说明收益成本并公平对比。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check agent_workbench tests
.\.venv\Scripts\python.exe -m agent_workbench eval
.\.venv\Scripts\python.exe -m agent_workbench app-eval
```

界面变化验证对应浏览器流程，不放宽断言掩盖失败。维护 pyproject.toml、uv.lock 与带哈希 requirements。
GitHub Actions、真实模型调用、镜像构建和部署只有实际成功后才报告通过。

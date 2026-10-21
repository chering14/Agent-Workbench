# 10 · FastAPI、后台任务与 SSE

代码入口：`server.py`。`create_app()` 组装配置、存储、AgentService、路由与静态资源，生命周期在启动时初始化数据库。

## HTTP 契约

| 操作 | 入口 |
| --- | --- |
| 登录、身份、退出 | POST /api/login，GET /api/me，POST /api/logout |
| 会话 | GET/POST /api/conversations |
| 历史消息 | GET /api/conversations/{id}/messages |
| 创建运行、查看历史运行 | POST/GET /api/conversations/{id}/runs |
| 运行结果、事件流 | GET /api/runs/{id}，GET /api/runs/{id}/events |
| 知识库 | GET/POST /api/documents，PUT /api/documents/{id} |
| 工单与确认 | GET /api/tickets，POST /api/drafts/{id}/decision |
| 管理状态与审计 | PATCH /api/tickets/{id}，GET /api/audits |
| 教程 | GET /api/tutorials，GET /api/tutorials/{slug} |

完整机器可读契约在 `/openapi.json`。401 表示未认证，403 表示权限或 CSRF 不满足，404 隐藏不可访问的资源，409 表示状态冲突，422 表示输入不符合契约。

## 为什么创建运行返回 202

模型调用可能持续数秒。API 先保存 queued 运行并返回 ID，FastAPI BackgroundTasks 在响应后调用同步业务函数。浏览器可以马上订阅进度，而无需一直等待最终结果。

后台任务仍在同一服务进程内，不是分布式任务队列。当前部署必须保持一个 worker；多进程同时初始化可能把另一个进程正在执行的任务当作重启残留。扩展前要引入任务租约、共享队列和独立 worker。

## SSE 的数据形状

```text
id: 42
data: {"stage":"retrieval","payload":{"count":1}}

event: done
data: {}
```

SSE 是服务器向浏览器的单向事件流。EventSource 自动带同源 Cookie，服务器检查运行归属。事件 ID 支持断线后按 Last-Event-ID 重放，结束事件告诉界面读取最终结果。

当前推送的是执行阶段，不是模型逐 token 输出。阶段与引用可直接映射到业务过程，也避免提前发布未审核文字。反向代理需要关闭 SSE 缓冲。

## 同步与异步

数据库与模型 HTTP 调用是同步实现，后台任务由框架线程池执行。SSE 生成器使用异步 sleep 等待，不占用一个持续阻塞的同步线程。生产规模下应进一步测量连接数、线程池和数据库等待。

实验：发送问题后刷新浏览器，再打开历史会话。界面会读取运行记录并重新订阅事件，而不是凭浏览器内存假装任务仍在执行。

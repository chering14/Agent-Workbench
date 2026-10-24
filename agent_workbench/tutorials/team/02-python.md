# 02 · 读懂项目需要的 Python

源码入口：team/contracts.py、team/db.py、team/api.py。

你会反复看到字典、类型注解、上下文管理器、线程和 async。字典保存一条实体：task["status"] 获取状态，t.update(status="queued") 修改对象。类型注解帮助表达意图；只有 Pydantic 模型校验等执行代码才真正拒绝非法数据。

```python
class TaskInput(Input):
    title: str = Field(min_length=1, max_length=150)
    member_id: str = "auto"
    dependencies: list[str] = Field(default_factory=list)
```

Field(default_factory=list) 为每个输入对象创建独立列表，避免共享可变默认值。model_validate 校验模型返回 JSON；model_dump 转成可存储的普通数据。extra="forbid" 拒绝未知字段，避免输入看似成功但字段其实被忽略。文件正文类不剥掉空白，因为缩进和换行可能影响程序含义。

with store.connect() as con 不是装饰语法：上下文管理器负责成功提交、异常回滚和关闭连接。文件和 HTTP 客户端也用 with 管理资源。try/finally 确保失败时关闭浏览器服务或结束进程。

FastAPI 的普通 def 路由可以交给线程池执行阻塞工作；SSE 用 async def 等待数据，不能在异步事件循环里执行长时间阻塞的文件命令。任务 runtime 使用 ThreadPoolExecutor，因此每个任务可以阻塞等待模型 HTTP，而不占用页面请求处理。

lambda t: t.update(...) 是一个小函数，传给 store.update。数据库在事务中读取最新 t，再运行这个修改函数，最后写回。若在事务外先读取再整条覆盖，两个线程可能互相丢失修改。

pathlib.Path 把路径当对象处理，避免手工拼接斜杠。resolve 得到解析后的绝对路径；is_relative_to 检查是否仍在预期根目录。路径安全还必须检查链接和禁止访问的文件，不能只做字符串 startswith。

自检：为什么函数标了 arguments: dict，外部输入仍可能不可信？答案：类型注解本身不验证 HTTP、JSON 或第三方插件内容；需要契约校验、授权和执行边界一起工作。

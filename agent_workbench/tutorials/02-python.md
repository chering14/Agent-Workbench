# 02 · Python、契约与模块边界

先读 `models.py`、`schemas.py`、`config.py`，再读业务流程。数据形状清楚之后，函数之间的关系就容易追踪。

## 类型注解与运行时校验

`question: str` 告诉阅读者和静态工具这里应该是字符串，但 Python 不会仅因这条注解就拒绝所有错误输入。API 和模型工具都使用 Pydantic 契约，在运行时检查长度、枚举和额外字段。

```python
class RunInput(Contract):
    question: str = Field(min_length=1, max_length=2000)
    intent: Literal["answer", "ticket"] = "answer"
```

`Contract` 的 `extra='forbid'` 会拒绝未定义字段。用户发送 `role='it_admin'` 不会改变权限，而是得到验证错误。身份来自服务器会话。

## dataclass 与 Pydantic

原始离线基线的 `models.py` 使用冻结 dataclass，表达简单的内部数据。完整应用的 `schemas.py` 使用 Pydantic，承担外部输入的解析与校验。`model_validate()` 校验对象，`model_dump()` 转成字典，`model_json_schema()` 生成工具 Schema。

`Settings` 用 dataclass 组织配置，密钥字段设置 `repr=False`，避免常规对象输出暴露密钥。配置本身仍应妥善保管，不能把整个进程环境写入日志。

## 上下文管理器

`Store.connection()` 使用 `@contextmanager`：正常完成时提交，异常时回滚，最后关闭连接。函数里每个 `with self.connection() as db:` 都有明确的事务和资源生命周期。

```python
with store.connection() as db:
    db.execute("SELECT ... WHERE id=?", (resource_id,))
```

SQL 参数通过占位符传入，不能用字符串拼接把用户内容放进 SQL。

## 异常如何跨层传播

模型适配器把网络、协议和状态码问题转为 `ProviderError`。AgentService 捕获后保存失败状态和简明错误码。API 把不存在的资源返回 404，把身份问题返回 401/403，把输入问题返回 422。

业务预期的失败应可理解；程序错误仍需要开发者查日志和测试。代码不会把模型供应商的原始错误正文直接返回界面。

## 阅读方法与练习

选一个函数，依次标出输入、返回值、读取的数据、修改的状态和可能的异常。然后沿调用者向外追一层，沿被调用者向内追一层。

练习：把问题最大长度从 2000 调到 1000，需要检查 API 契约、前端输入限制、测试和教程。只改网页属性无法约束直接调用 API 的用户。

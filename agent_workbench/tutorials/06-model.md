# 06 · 模型接口与工具调用协议

代码入口：`providers.py`、`schemas.py`。核心边界是 `complete(messages, tools)`，返回工具请求与 token 使用量。

## 真正的工具调用流程

1. 应用把系统规则、历史、当前请求和工具定义发给模型。
2. 模型返回工具名称、参数与调用 ID。
3. 应用检查工具名、参数、身份、业务前置条件，然后执行函数。
4. 应用把结果作为 tool 消息返回，关联原来的调用 ID。
5. 模型据此继续选择工具，或调用 finish_answer 提交结果。

模型提出调用请求，执行权在应用。不能把模型产生的任意函数名交给 `eval()`，也不能允许模型直接决定自己具有哪种权限。

## 消息格式

```json
{"role":"assistant","tool_calls":[{"id":"call_1","type":"function","function":{"name":"search_knowledge","arguments":"{\"query\":\"VPN\"}"}}]}
```

应用执行后返回 `role=tool`、`tool_call_id=call_1` 和 JSON 字符串结果。保留 ID 是为了让模型识别哪个结果属于哪个调用。

工具 Schema 从 Pydantic 契约生成，关闭额外字段并标记必填字段。即使提供商支持 strict Schema，应用仍进行本地校验。

## 接口配置

`CompatibleProvider` 调用 `LLM_BASE_URL + /chat/completions`，支持符合所用协议的 OpenAI 兼容端点。默认 OpenAI 端点用 `max_completion_tokens`，其他端点用 `max_tokens`。工具能力、strict 字段和 required 工具选择需要提供商实际支持。

本地 Ollama 等服务也需要启用兼容接口并选择支持工具的模型；若本地服务不验证 Key，可在本地 `.env` 设置占位值。不要假定“兼容”意味着所有参数完全一致。

## 错误、重试与费用

网络异常和 429/部分 5xx 最多重试一次；401 等认证错误直接失败，避免无效重复。错误码返回界面，供应商原始正文和 Authorization 不写入普通日志。

token 用量用于预算和比较。没有返回 usage 时采用保守字节估算，并明确标记。费用只有在真实模式、usage 已知且输入输出单价都配置时才估算；它不能替代供应商账单，失败重试和额外服务可能产生未计入费用。

## 如何测试而不付费

HTTP MockTransport 返回协议级假响应，可以验证 URL、请求结构、解析、重试和错误处理。它不证明真实供应商可用或模型质量达标。配置后运行 `check-model` 做真实接口冒烟，再用业务样本评测。

参考官方文档：`https://developers.openai.com/api/docs/guides/function-calling`。

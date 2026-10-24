# 05 · 模型接口与工具调用循环

源码入口：team/model.py、team/runtime.py 的 execute。

ChatModel 发送 messages、tools、tool_choice 和输出限制到 Base URL 后的 /chat/completions。tools 中包含函数名、用途和参数 JSON Schema。本版本要求每步恰好一个 function 调用，关闭并行工具调用，便于审批和持久化；不支持的供应商会返回清楚错误，不会静默退成规则演示。

```text
系统约定 + 项目上下文 → 模型选择 read_file
read_file 的真实结果 → tool 消息
已有历史 + 工具结果 → 模型选择 edit_file
编辑结果 → 模型选择 run_command
人工确认 + 真实命令结果 → 模型选择 finish_task
```

模型返回的是调用请求，Python 程序才真正执行。runtime 检查函数名是否授权，再用对应 Pydantic 类验证 arguments。工具结果带 tool_call_id 回给模型，与上一条 assistant.tool_calls 对应；丢失 ID 或交换顺序会破坏协议。

某些兼容推理模型需要继续传 reasoning_content，因此适配器保留该字段。但这不是所有供应商完全兼容的保证。本版本以 Chat Completions 为统一协议，OpenAI 主机使用 max_completion_tokens，其余兼容接口使用 max_tokens；模型必须支持相关参数。

一次请求最多等待 90 秒，响应超过 2MB 拒绝。网络或格式失败不会偷偷重试可能已经计费的请求；任务标记失败，用户选择下一步。输入和输出 Token 来自响应 usage。若缺失，则账本标注估算。

输出限制控制一次生成长度，步骤限制控制任务最多调用模型多少次，项目预算控制累计支出。三者不是同一概念。一个任务连续写十个文件可能超过较小步骤上限，即使没有用完项目预算。

本地协议测试使用 httpx.MockTransport，脚本模型测试使用 ScriptModel，均不是真实大模型。真实供应商验证应先安排小任务，检查工具调用、usage、模型 ID、错误消息和账单，再扩大需求。

自检：模型写“测试通过”就代表通过吗？答案：不代表。要核对命令工具的 exit_code、输出和实际用例；finish_task 的文字报告是模型自述，不能覆盖证据。

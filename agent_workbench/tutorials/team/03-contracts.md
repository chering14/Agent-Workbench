# 03 · 契约、API 与本机边界

源码入口：team/contracts.py、team/api.py。

一次输入经历浏览器表单、JSON 请求、Pydantic 校验、业务规则、数据库或 runtime。前端限制字符数用于体验，服务端限制才是可靠边界。不要因为“只有本机用户”就让模型指定任意工具或路径。

ProjectInput 限制名称、说明、Token 预算和成员列表；MemberInput 限制接口、档位、步骤数；TaskInput 定义负责人和依赖；ControlInput 只接受列出的操作。模型工具使用同一组显式契约，函数描述用于让模型理解，模型 JSON Schema 用于告知形状，服务端 model_validate 才负责执行时检查。

API 主要分成：GET /api/bootstrap 获取页面初始状态；/api/projects 管项目；/api/members 管成员；/api/projects/{id}/tasks 建任务；/api/tasks/{id}/control 改状态；/api/extensions 管扩展；/api/tutorials 读课程。

无账号登录的边界是单用户本机进程。启动生成随机页面写令牌，bootstrap 返回后由前端在写请求头 X-AW-Token 携带。服务端也检查 Host 和 Origin，拒绝跨站请求。它不是用户密码，也不是可以公开部署的身份体系。

```javascript
fetch(path, {
  method: "POST",
  headers: {"Content-Type": "application/json", "X-AW-Token": state.token},
  body: JSON.stringify(data)
});
```

校验错误只返回字段位置、类型和错误信息，不返回原始输入；否则 API Key 长度错误可能被默认 Pydantic 错误响应原样回显。数据库实体不存在返回 404，状态冲突返回 409，请求格式不合法返回 422。页面显示这些信息，不能用“保存成功”掩盖服务端拒绝。

浏览器拿到任务详情时，不返回完整模型历史和恢复快照；待确认操作只暴露工具名称与参数等必要字段。密钥字段永远不放在响应里，只有 key_configured 布尔值。

自检：修改浏览器表单能不能把成员没有的工具权限加给任务？答案：不能。权限来自服务端成员记录，runtime 根据能力生成工具白名单并再次验证返回函数名。

# 04 · SQLite、事件与可恢复状态

源码入口：team/db.py。

本项目用 SQLite 减少本机部署步骤。entities 表存 project/member/task/extension 的 JSON，events 表存可追加的群事件，calls 表存模型用量账本。JSON 实体利于小项目迭代；代价是复杂查询和字段约束不像标准化列那样强。关键预算和计划事务仍由数据库处理。

每次连接开启 WAL 与 busy_timeout。WAL 让读取与写入更容易协同，但 SQLite 仍是有限写并发的数据库，不支持随意扩成多个服务进程。TeamStore.update 使用 BEGIN IMMEDIATE，在写事务中读最新对象，再修改并保存，避免多线程的读后覆盖。

events 有递增 seq，保存 project_id、task_id、kind、actor、payload 和时间。前端断线后按 seq 补读。事件记录供显示、调试和复盘；它不是完整的事件溯源架构，任务当前状态仍以 entities 为准，也没有宣称每个文件副作用都与事件处于同一事务。

accept_plan 把所有子任务插入和计划已采用标记放在一个事务。假设第三项插入失败，前两项也回滚，父任务不标为采用。重复点击会拒绝，避免计划被展开两次。这比循环创建任务后再补标记可靠。

启动时 queued/running/waiting_approval 统一标记 interrupted，原来的 pending 清空。原因是上次进程可能在执行命令中退出，重放一次不一定安全；已写文件仍在副本，用户可选择继续。已完成任务和历史事件保留。

调用账本预留值在异常退出后仍保留为估算，因为不能假设供应商没有计费。数据库 schema 用 PRAGMA user_version 管理，当前为 1；未来升级应写明确迁移，遇到更高版本拒绝启动，不能删除数据库“修复”。

备份时先正常停止服务，再一起复制数据目录；正在运行时仅复制 team.db 可能漏掉 WAL 内容。恢复后密钥还需要对应操作系统凭据或 .env，数据库备份不包含它们。

自检：为什么不能把 Token 计数放到 Python 全局变量？答案：进程重启会丢失，多线程检查后更新会有竞态，无法恢复每次调用的计费证据。

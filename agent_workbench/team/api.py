"""Loopback-only application; no user accounts, with a per-launch browser write token."""

import asyncio
import json
import os
import secrets
from contextlib import asynccontextmanager
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .contracts import (
    ControlInput,
    ExtensionInput,
    MemberInput,
    MessageInput,
    ProjectInput,
    TaskInput,
)
from .credentials import Credentials
from .db import TeamStore
from .extensions import validate_extension
from .runtime import TeamRuntime
from .workspace import file_map, safe_path


def create_team_app(data_dir=None, model=None, credentials=None, start_worker=True):
    load_dotenv(Path.cwd() / ".env", override=False)
    data_dir = Path(data_dir or os.getenv("TEAM_DATA_DIR", "var/team")).resolve()
    store = TeamStore(data_dir / "team.db")
    credentials = credentials or Credentials()
    runtime = TeamRuntime(store, credentials, data_dir, model=model)
    token = secrets.token_urlsafe(32)

    @asynccontextmanager
    async def lifespan(app):
        store.initialize()
        if not store.list("member"):
            for name, role, tier in [
                ("演示开发员", "固定规则生成示例文件", "economy"),
                ("演示审查员", "固定规则生成交接说明", "expert"),
            ]:
                member = MemberInput(
                    name=name, role=role, provider="demo", model="rules-demo", tier=tier
                ).model_dump(exclude={"api_key"})
                store.create("member", member)
            if os.getenv("LLM_MODEL"):
                member = MemberInput(
                    name="我的模型",
                    provider="compatible",
                    model=os.environ["LLM_MODEL"],
                    base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
                    key_env="LLM_API_KEY",
                ).model_dump(exclude={"api_key"})
                store.create("member", member)
        if not store.list("project"):
            root = data_dir / "demo-project"
            root.mkdir(parents=True, exist_ok=True)
            project = store.create(
                "project",
                ProjectInput(
                    name="我的第一个项目",
                    root=str(root),
                    brief="在这里体验规划、分工、成果交接。添加真实模型后可处理自己的软件需求。",
                ).model_dump(),
            )
            store.event(
                project["id"],
                "message",
                "工作台",
                {
                    "text": "欢迎来到本地工作群。可先选择「自动规划」体验离线流程，或在成员中添加真实模型。"
                },
            )
        builtin = Path(
            str(files("agent_workbench").joinpath("team_assets/skills/software-delivery"))
        )
        if not store.list("extension"):
            store.create(
                "extension",
                {
                    "name": "软件交付",
                    "kind": "skill",
                    "config": validate_extension("skill", {"path": str(builtin)}),
                    "enabled": True,
                    "tools": [],
                    "tested": True,
                    "error": "",
                },
            )
        if start_worker:
            runtime.start()
        yield
        runtime.close()

    app = FastAPI(
        title="Agent Workbench · Local Teams",
        version="2.0.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )
    app.state.store, app.state.runtime = store, runtime

    @app.middleware("http")
    async def local_boundary(request, call_next):
        host = urlsplit("http://" + request.headers.get("host", "")).hostname
        if host not in {"localhost", "127.0.0.1", "::1"}:
            return JSONResponse({"detail": "仅接受本机访问"}, status_code=403)
        origin = request.headers.get("origin")
        if (
            origin and urlsplit(origin).netloc != request.headers.get("host")
        ) or request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "拒绝跨站请求"}, status_code=403)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if not secrets.compare_digest(request.headers.get("x-aw-token", ""), token):
                return JSONResponse({"detail": "页面令牌失效，请刷新工作台"}, status_code=403)
            try:
                if int(request.headers.get("content-length", "0")) > 1_000_000:
                    return JSONResponse({"detail": "请求过大"}, status_code=413)
            except ValueError:
                return JSONResponse({"detail": "请求格式错误"}, status_code=400)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if not request.url.path.startswith("/preview/"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-src 'self'; frame-ancestors 'none'"
            )
            response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(KeyError)
    async def missing(request, error):
        return JSONResponse({"detail": "记录不存在"}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(request, error):
        return JSONResponse({"detail": str(error)[:1500]}, status_code=409)

    @app.exception_handler(OSError)
    async def io_error(request, error):
        return JSONResponse({"detail": "本地文件操作失败，请检查路径和权限"}, status_code=400)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        # Pydantic's default errors include raw input, potentially containing a submitted key.
        return JSONResponse(
            {
                "detail": [
                    {"loc": list(e["loc"]), "type": e["type"], "msg": e["msg"]}
                    for e in error.errors()
                ]
            },
            status_code=422,
        )

    def validate_members(ids):
        for id in ids:
            store.get(id, "member")

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": "2.0.0", "local": True, "login_required": False}

    @app.get("/api/bootstrap")
    def bootstrap():
        return {
            "token": token,
            "projects": store.list("project"),
            "members": [credentials.public(m) for m in store.list("member")],
            "extensions": store.list("extension"),
            "cwd": str(Path.cwd()),
            "version": "2.0.0",
        }

    @app.post("/api/projects", status_code=201)
    def create_project(data: ProjectInput):
        validate_members(data.member_ids)
        root = (
            Path(data.root).expanduser().resolve()
            if data.root
            else data_dir / "projects" / secrets.token_hex(8)
        )
        if not data.root:
            root.mkdir(parents=True)
        if not root.is_dir() or root == Path(root.anchor) or root.is_symlink():
            raise ValueError("请选择已存在的项目文件夹；不能选择磁盘根目录")
        file_map(root)
        return store.create("project", data.model_dump() | {"root": str(root)})

    @app.put("/api/projects/{id}")
    def update_project(id: str, data: ProjectInput):
        validate_members(data.member_ids)
        project = store.get(id, "project")
        if data.root and str(Path(data.root).resolve()) != project["root"]:
            raise ValueError("已有项目不支持更换目录，请创建新项目")
        return store.update(id, lambda p: p.update(data.model_dump(exclude={"root"})))

    def public_task(task, detailed=False):
        result = {k: v for k, v in task.items() if k not in {"history", "pending", "recovery"}}
        if not detailed:
            result["changes"] = [
                {k: c[k] for k in ("path", "before", "after")} for c in task["changes"]
            ]
        if task.get("pending"):
            result["pending"] = {k: task["pending"][k] for k in ("name", "arguments", "approved")}
        return result

    @app.get("/api/projects/{id}")
    def project_state(id: str):
        project = store.get(id, "project")
        return {
            "project": project,
            "tasks": [public_task(t) for t in store.list("task") if t["project_id"] == id],
            "usage": store.usage(id),
            "members": [credentials.public(m) for m in store.list("member")],
        }

    @app.get("/api/projects/{id}/events")
    async def events(id: str, request: Request, after: int = 0):
        store.get(id, "project")
        try:
            cursor = max(after, int(request.headers.get("last-event-id", "0")))
        except ValueError:
            raise HTTPException(400, "事件游标无效") from None

        async def generate():
            nonlocal cursor
            while not await request.is_disconnected():
                for event in store.events(id, cursor):
                    cursor = event["seq"]
                    yield f"id: {cursor}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                yield ": heartbeat\n\n"
                await asyncio.sleep(1)

        return StreamingResponse(generate(), media_type="text/event-stream")

    @app.get("/api/projects/{id}/history")
    def history(id: str, after: int = 0):
        store.get(id, "project")
        return store.events(id, after)

    @app.post("/api/projects/{id}/messages", status_code=201)
    def message(id: str, data: MessageInput):
        store.get(id, "project")
        selected = data.member_id
        if not selected:
            mentioned = [m for m in runtime.members(id) if ("@" + m["name"]) in data.content]
            if len(mentioned) == 1:
                selected = mentioned[0]["id"]
        store.event(id, "message", "你", {"text": data.content})
        if data.action != "message" or selected:
            task = runtime.create_task(
                id,
                TaskInput(
                    title=data.content[:100],
                    instruction=data.content,
                    member_id=selected or "auto",
                    start=True,
                    kind="plan" if data.action == "plan" else "work",
                ),
            )
            return {"task": public_task(task)}
        return {"ok": True}

    @app.post("/api/projects/{id}/tasks", status_code=201)
    def task(id: str, data: TaskInput):
        return public_task(runtime.create_task(id, data))

    @app.get("/api/tasks/{id}")
    def task_detail(id: str):
        return public_task(store.get(id, "task"), True)

    @app.post("/api/tasks/{id}/control")
    def control(id: str, data: ControlInput):
        return public_task(runtime.control(id, data), True)

    @app.get("/api/tasks/{id}/files")
    def task_files(id: str):
        task = store.get(id, "task")
        return list(file_map(Path(task["workspace"]) / "work")) if task["workspace"] else []

    @app.get("/api/tasks/{id}/file")
    def task_file(id: str, path: str):
        task = store.get(id, "task")
        if not task["workspace"]:
            raise ValueError("任务尚未创建工作副本")
        file = safe_path(Path(task["workspace"]) / "work", path)
        if not file.is_file() or file.stat().st_size > 1_000_000:
            raise ValueError("文件不存在或过大")
        try:
            return {"path": path, "content": file.read_text(encoding="utf-8")}
        except UnicodeError:
            raise ValueError("该文件不是 UTF-8 文本") from None

    @app.get("/api/tasks/{id}/screenshot")
    def screenshot(id: str):
        task = store.get(id, "task")
        path = Path(task["workspace"]) / "artifacts/browser.png"
        if not path.is_file():
            raise HTTPException(404, "尚无浏览器截图")
        return FileResponse(path, media_type="image/png")

    @app.get("/preview/{id}/{path:path}")
    def preview(id: str, path: str):
        task = store.get(id, "task")
        if not task["workspace"]:
            raise HTTPException(404)
        file = safe_path(Path(task["workspace"]) / "work", path)
        if not file.is_file() or file.suffix.lower() not in {
            ".html",
            ".css",
            ".js",
            ".svg",
            ".png",
            ".jpg",
            ".json",
        }:
            raise HTTPException(404)
        return FileResponse(
            file,
            headers={
                "Content-Security-Policy": "sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline' 'self'; style-src 'unsafe-inline' 'self'; img-src 'self' data:; connect-src 'none'; frame-ancestors 'self'"
            },
        )

    @app.post("/api/members", status_code=201)
    def add_member(data: MemberInput):
        from .db import uid

        id = uid("member")
        credentials.save(id, data.api_key)
        member = store.create("member", data.model_dump(exclude={"api_key"}), id=id)
        return credentials.public(member)

    @app.put("/api/members/{id}")
    def update_member(id: str, data: MemberInput):
        store.get(id, "member")
        credentials.save(id, data.api_key)
        return credentials.public(
            store.update(id, lambda m: m.update(data.model_dump(exclude={"api_key"})))
        )

    @app.post("/api/extensions", status_code=201)
    def add_extension(data: ExtensionInput):
        config = validate_extension(data.kind, data.config)
        return store.create(
            "extension",
            data.model_dump() | {"config": config, "tools": [], "tested": False, "error": ""},
        )

    @app.put("/api/extensions/{id}")
    def update_extension(id: str, data: ExtensionInput):
        store.get(id, "extension")
        config = validate_extension(data.kind, data.config)
        return store.update(
            id,
            lambda e: e.update(
                data.model_dump() | {"config": config, "tools": [], "tested": False, "error": ""}
            ),
        )

    @app.post("/api/extensions/{id}/inspect")
    def inspect_extension(id: str):
        e = store.get(id, "extension")
        if not e["enabled"]:
            raise ValueError("请先启用该扩展，再检查连接")
        try:
            return runtime.extensions.inspect(e)
        except Exception as error:
            message = "连接检查失败：" + type(error).__name__ + "。请检查命令、服务地址及依赖。"
            store.update(id, lambda e: e.update(error=message, tested=False))
            raise HTTPException(502, message) from None

    @app.get("/api/tutorials")
    def tutorials():
        return json.loads(
            files("agent_workbench")
            .joinpath("tutorials/team/index.json")
            .read_text(encoding="utf-8")
        )

    @app.get("/api/tutorials/{slug}")
    def tutorial(slug: str):
        if slug not in {c["slug"] for c in tutorials()}:
            raise HTTPException(404)
        return {
            "content": files("agent_workbench")
            .joinpath(f"tutorials/team/{slug}.md")
            .read_text(encoding="utf-8")
        }

    web = files("agent_workbench").joinpath("team_web")
    app.mount("/static", StaticFiles(directory=str(web)), name="static")

    @app.get("/")
    def index():
        return FileResponse(str(web.joinpath("index.html")))

    return app

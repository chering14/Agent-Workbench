"""HTTP application: authentication, ownership, asynchronous runs, and SSE events."""

import asyncio
import json
import secrets
from contextlib import asynccontextmanager
from importlib.resources import files
from typing import Literal
from urllib.parse import urlsplit

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field

from .agent import AgentService
from .auth import LoginLimiter, hash_password, verify_password
from .config import Settings
from .schemas import (
    Contract,
    ConversationInput,
    DocumentInput,
    DraftDecision,
    LoginInput,
    RunInput,
)
from .storage import Store


class TicketStatusInput(Contract):
    status: Literal["open", "in_progress", "resolved"] = Field()


def create_app(settings=None, provider_factory=None):
    settings = settings or Settings.from_env()
    store = Store(settings.db_path)
    service = AgentService(store, settings, provider_factory)
    limiter = LoginLimiter()
    dummy_hash = hash_password("constant-time-dummy-password")

    @asynccontextmanager
    async def lifespan(application):
        store.initialize(settings)
        yield

    app = FastAPI(
        title="Agent Workbench", version="1.0.0", lifespan=lifespan, docs_url=None, redoc_url=None
    )
    app.state.store, app.state.agent, app.state.settings = store, service, settings

    @app.middleware("http")
    async def browser_boundary(request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc != request.headers.get("host"):
                return Response("cross-origin mutation denied", status_code=403)
            try:
                if int(request.headers.get("content-length", "0")) > 1_000_000:
                    return Response("request too large", status_code=413)
            except ValueError:
                return Response("invalid content length", status_code=400)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def identity(request: Request):
        user = store.session(request.cookies.get("aw_session"))
        if not user:
            raise HTTPException(401, "请先登录")
        if request.method not in {"GET", "HEAD"} and not secrets.compare_digest(
            request.headers.get("x-csrf-token", ""), user["csrf"]
        ):
            raise HTTPException(403, "缺少有效的 CSRF token，请刷新页面")
        return user

    def administrator(user=Depends(identity)):
        if user["role"] != "it_admin":
            raise HTTPException(403, "需要管理员权限")
        return user

    def require_conversation(conversation_id, user):
        if not store.owns_conversation(conversation_id, user):
            raise HTTPException(404, "会话不存在")

    @app.get("/api/health")
    def health():
        return {
            "status": "ok",
            "mode": settings.mode,
            "version": "1.0.0",
            "review_enabled": settings.review_enabled,
            "demo_accounts": settings.seed_demo,
            "model": settings.model if settings.mode == "live" else "规则演示（无模型调用）",
        }

    @app.post("/api/login")
    def login(data: LoginInput, request: Request, response: Response):
        if not limiter.allow(request.client.host if request.client else "unknown"):
            raise HTTPException(429, "登录尝试过多，请一分钟后重试")
        user = store.user_by_name(data.username)
        valid = verify_password(data.password, user["password_hash"] if user else dummy_hash)
        if not user or not valid:
            raise HTTPException(401, "用户名或密码错误")
        token, csrf = store.create_session(user["id"], settings.session_hours)
        response.set_cookie(
            "aw_session",
            token,
            max_age=settings.session_hours * 3600,
            httponly=True,
            secure=settings.cookie_secure,
            samesite="strict",
        )
        return {
            "id": user["id"],
            "username": user["username"],
            "tenant_id": user["tenant_id"],
            "role": user["role"],
            "csrf": csrf,
        }

    @app.get("/api/me")
    def me(user=Depends(identity)):
        return user

    @app.post("/api/logout")
    def logout(request: Request, response: Response, user=Depends(identity)):
        store.logout(request.cookies["aw_session"])
        response.delete_cookie("aw_session")
        return {"ok": True}

    @app.get("/api/conversations")
    def conversations(user=Depends(identity)):
        return store.conversations(user)

    @app.post("/api/conversations", status_code=201)
    def create_conversation(data: ConversationInput, user=Depends(identity)):
        return store.create_conversation(user, data.title)

    @app.get("/api/conversations/{conversation_id}/messages")
    def messages(conversation_id: str, user=Depends(identity)):
        require_conversation(conversation_id, user)
        return store.messages(conversation_id)

    @app.post("/api/conversations/{conversation_id}/runs", status_code=202)
    def run(
        conversation_id: str, data: RunInput, background: BackgroundTasks, user=Depends(identity)
    ):
        require_conversation(conversation_id, user)
        try:
            run_id = store.create_run(user, conversation_id, data.question, data.intent)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        background.add_task(service.execute, run_id, user)
        return {"run_id": run_id}

    @app.get("/api/conversations/{conversation_id}/runs")
    def conversation_runs(conversation_id: str, user=Depends(identity)):
        require_conversation(conversation_id, user)
        return store.runs(conversation_id)

    @app.get("/api/runs/{run_id}")
    def run_details(run_id: str, user=Depends(identity)):
        result = store.run(run_id, user)
        if not result:
            raise HTTPException(404, "运行不存在")
        return result

    @app.get("/api/runs/{run_id}/events")
    async def events(run_id: str, request: Request, user=Depends(identity)):
        if not store.run(run_id, user):
            raise HTTPException(404, "运行不存在")
        try:
            after = max(0, int(request.headers.get("last-event-id", "0")))
        except ValueError:
            raise HTTPException(400, "invalid Last-Event-ID") from None

        async def generate():
            cursor, ticks = after, 0
            while not await request.is_disconnected():
                for event in store.events(run_id, cursor):
                    cursor = event["id"]
                    yield f"id: {cursor}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                current = store.run(run_id, user)
                if current["status"] in {"completed", "failed"}:
                    # Include the final terminal event if a commit raced with the last read.
                    for event in store.events(run_id, cursor):
                        yield f"id: {event['id']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                    yield "event: done\ndata: {}\n\n"
                    break
                ticks += 1
                if ticks % 40 == 0:
                    yield ": keepalive\n\n"
                await asyncio.sleep(0.25)

        return StreamingResponse(
            generate(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"}
        )

    @app.get("/api/documents")
    def documents(user=Depends(identity)):
        return store.documents(user)

    @app.post("/api/documents", status_code=201)
    def add_document(data: DocumentInput, user=Depends(administrator)):
        return store.save_document(user, **data.model_dump())

    @app.put("/api/documents/{document_id}")
    def update_document(document_id: str, data: DocumentInput, user=Depends(administrator)):
        if document_id not in {document["id"] for document in store.documents(user)}:
            raise HTTPException(404, "资料不存在")
        return store.save_document(user, document_id=document_id, **data.model_dump())

    @app.get("/api/tickets")
    def tickets(user=Depends(identity)):
        return {"tickets": store.tickets(user), "drafts": store.drafts(user)}

    @app.post("/api/drafts/{draft_id}/decision")
    def decide(draft_id: str, data: DraftDecision, user=Depends(identity)):
        try:
            return store.decide_draft(draft_id, user, data.approve)
        except KeyError:
            raise HTTPException(404, "草稿不存在") from None
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @app.patch("/api/tickets/{ticket_id}")
    def update_ticket(ticket_id: str, data: TicketStatusInput, user=Depends(administrator)):
        try:
            return store.update_ticket(ticket_id, user, data.status)
        except KeyError:
            raise HTTPException(404, "工单不存在") from None

    @app.get("/api/audits")
    def audits(user=Depends(administrator)):
        return store.audits(user)

    @app.get("/api/tutorials")
    def tutorials():
        return json.loads(
            files("agent_workbench").joinpath("tutorials/index.json").read_text(encoding="utf-8")
        )

    @app.get("/api/tutorials/{slug}")
    def tutorial(slug: str):
        chapters = tutorials()
        if slug not in {chapter["slug"] for chapter in chapters}:
            raise HTTPException(404, "章节不存在")
        return {
            "content": files("agent_workbench")
            .joinpath(f"tutorials/{slug}.md")
            .read_text(encoding="utf-8")
        }

    web = files("agent_workbench").joinpath("web")
    app.mount("/static", StaticFiles(directory=str(web)), name="static")

    @app.get("/")
    def index():
        return FileResponse(str(web.joinpath("index.html")))

    return app

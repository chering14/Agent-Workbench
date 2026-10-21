"""Application contracts: ownership, approval, evidence, and failure behavior."""

import json
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_workbench.agent import AgentService
from agent_workbench.config import Settings
from agent_workbench.providers import (
    CompatibleProvider,
    DemoProvider,
    ModelReply,
    ProviderError,
    ToolCall,
)
from agent_workbench.retrieval import Retriever, split_document
from agent_workbench.server import create_app
from agent_workbench.storage import Store


@pytest.fixture
def settings(tmp_path):
    return Settings(db_path=str(tmp_path / "app.db"))


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app) as connection:
        yield connection


def login(client, username="employee", password="AgentDemo!2026"):
    response = client.post("/api/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf"]
    return response.json()


def ask(client, question="VPN 连接失败", intent="answer", conversation_id=None):
    if conversation_id is None:
        response = client.post("/api/conversations", json={"title": question[:35]})
        assert response.status_code == 201, response.text
        conversation_id = response.json()["id"]
    response = client.post(
        f"/api/conversations/{conversation_id}/runs", json={"question": question, "intent": intent}
    )
    assert response.status_code == 202, response.text
    run = client.get("/api/runs/" + response.json()["run_id"]).json()
    return run, conversation_id


def test_static_health_and_schema(client):
    assert "Agent Workbench" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/api/health").json()["mode"] == "demo"
    assert "/api/login" in client.get("/openapi.json").json()["paths"]


def test_authentication_csrf_and_logout(client):
    assert client.get("/api/documents").status_code == 401
    user = login(client)
    assert "password_hash" not in user
    assert "HttpOnly" in client.cookies.jar._cookies["testserver.local"]["/"]["aw_session"]._rest
    assert (
        client.post(
            "/api/conversations", json={"title": "x"}, headers={"X-CSRF-Token": "wrong"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/conversations", json={"title": "x"}, headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert client.post("/api/logout").status_code == 200
    assert client.get("/api/me").status_code == 401


def test_login_throttle(client):
    for _ in range(10):
        assert (
            client.post("/api/login", json={"username": "missing", "password": "bad"}).status_code
            == 401
        )
    assert (
        client.post("/api/login", json={"username": "missing", "password": "bad"}).status_code
        == 429
    )


def test_demo_answer_and_sse(client):
    login(client)
    run, conversation_id = ask(client)
    assert run["status"] == "completed", run
    assert run["result"]["status"] == "evidence_found"
    assert run["result"]["citations"][0]["document_id"] == "IT-001"
    assert run["result"]["review"]["approved"] is True
    assert run["result"]["mode"] == "demo"
    stream = client.get(f"/api/runs/{run['id']}/events")
    assert "text/event-stream" in stream.headers["content-type"]
    assert '"stage": "retrieval"' in stream.text
    assert "event: done" in stream.text
    messages = client.get(f"/api/conversations/{conversation_id}/messages").json()
    assert [message["role"] for message in messages] == ["user", "assistant"]


@pytest.mark.parametrize(
    "question", ["火星基地停机坪坐标", "VPN 月费", "生产密钥轮换", "猎户计划客户名单"]
)
def test_absence_and_permissions_produce_no_factual_answer(client, question):
    login(client)
    run, _ = ask(client, question)
    assert run["result"]["status"] == "insufficient_evidence", run
    assert not run["result"]["citations"]


def test_tenant_and_role_documents(client):
    login(client)
    ids = {document["id"] for document in client.get("/api/documents").json()}
    assert "SEC-001" not in ids and "BETA-001" not in ids
    assert (
        client.post(
            "/api/documents", json={"title": "test", "content": "x" * 20, "roles": ["employee"]}
        ).status_code
        == 403
    )
    login(client, "admin", "AgentAdmin!2026")
    run, _ = ask(client, "生产密钥轮换")
    assert run["result"]["citations"][0]["document_id"] == "SEC-001"
    login(client, "beta", "AgentBeta!2026")
    assert {document["id"] for document in client.get("/api/documents").json()} == {"BETA-001"}


def test_conversation_run_and_draft_ownership(client):
    login(client)
    run, conversation_id = ask(client, "VPN 无法使用，请处理", "ticket")
    draft = run["result"]["draft"]
    login(client, "beta", "AgentBeta!2026")
    assert client.get(f"/api/conversations/{conversation_id}/messages").status_code == 404
    assert client.get(f"/api/runs/{run['id']}").status_code == 404
    assert client.get(f"/api/runs/{run['id']}/events").status_code == 404
    assert (
        client.post(f"/api/drafts/{draft['id']}/decision", json={"approve": True}).status_code
        == 404
    )


def test_ticket_approval_idempotency_and_admin_status(client):
    login(client)
    run, _ = ask(client, "VPN 故障请协助", "ticket")
    assert run["result"]["status"] == "awaiting_confirmation"
    assert client.get("/api/tickets").json()["tickets"] == []
    draft_id = run["result"]["draft"]["id"]
    first = client.post(f"/api/drafts/{draft_id}/decision", json={"approve": True})
    second = client.post(f"/api/drafts/{draft_id}/decision", json={"approve": True})
    assert first.status_code == 200 and first.json()["id"] == second.json()["id"]
    ticket_id = first.json()["id"]
    assert len(client.get("/api/tickets").json()["tickets"]) == 1
    assert client.patch(f"/api/tickets/{ticket_id}", json={"status": "resolved"}).status_code == 403
    login(client, "admin", "AgentAdmin!2026")
    assert (
        client.patch(f"/api/tickets/{ticket_id}", json={"status": "resolved"}).json()["status"]
        == "resolved"
    )
    assert any(audit["action"] == "ticket_created" for audit in client.get("/api/audits").json())


def test_ticket_rejection(client):
    login(client)
    run, _ = ask(client, "账号需要人工协助", "ticket")
    path = f"/api/drafts/{run['result']['draft']['id']}/decision"
    assert client.post(path, json={"approve": False}).json()["status"] == "rejected"
    assert client.post(path, json={"approve": True}).status_code == 409
    assert not client.get("/api/tickets").json()["tickets"]


def test_document_import_update_and_snapshot_citations(client):
    login(client, "admin", "AgentAdmin!2026")
    data = {
        "title": "打印机故障指南",
        "content": "打印机无法打印时，先检查纸张是否充足，再检查打印队列。",
        "roles": ["employee", "it_admin"],
    }
    response = client.post("/api/documents", json=data)
    assert response.status_code == 201
    document_id = response.json()["id"]
    run, _ = ask(client, "打印机故障指南")
    citation = run["result"]["citations"][0]
    assert citation["document_id"] == document_id and citation["version"] == 1
    data["content"] = "打印机无法打印时，按照新版本流程联系服务台。"
    assert client.put(f"/api/documents/{document_id}", json=data).json()["version"] == 2
    old = client.get(f"/api/runs/{run['id']}").json()["result"]["citations"][0]
    assert "打印队列" in old["quote"] and old["version"] == 1


def test_untrusted_extra_fields_and_empty_questions(client):
    login(client)
    conversation = client.post("/api/conversations", json={"title": "test"}).json()["id"]
    assert (
        client.post(
            f"/api/conversations/{conversation}/runs", json={"question": " ", "intent": "answer"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/conversations/{conversation}/runs", json={"question": "VPN", "role": "it_admin"}
        ).status_code
        == 422
    )


def initialized(settings):
    store = Store(settings.db_path)
    store.initialize(settings)
    user = store.user_by_name("employee")
    conversation = store.create_conversation(user, "test")
    return store, user, conversation["id"]


class ScriptedProvider:
    def __init__(self, calls):
        self.calls = iter(calls)

    def complete(self, messages, tools, timeout=None):
        return ModelReply([next(self.calls)], 20, 10)


def test_forged_citation_is_never_published(settings):
    store, user, conversation = initialized(settings)
    final = ToolCall(
        "finish_answer",
        {
            "status": "answered",
            "text": "捏造的结论",
            "citations": [{"chunk_id": "fake", "quote": "fake"}],
        },
    )
    provider = ScriptedProvider([final, final, final])
    service = AgentService(store, settings, lambda: provider)
    run_id = store.create_run(user, conversation, "VPN", "answer")
    service.execute(run_id, user)
    run = store.run(run_id)
    assert run["status"] == "failed"
    assert run["result"]["citations"] == []
    assert "捏造的结论" not in run["result"]["text"]


def test_model_cannot_grant_its_own_ticket_intent(settings):
    store, user, conversation = initialized(settings)
    draft = ToolCall(
        "prepare_ticket", {"title": "unauthorized", "description": "a" * 20, "priority": "high"}
    )
    provider = ScriptedProvider([draft, draft, draft])
    run_id = store.create_run(user, conversation, "VPN", "answer")
    AgentService(store, settings, lambda: provider).execute(run_id, user)
    assert store.run(run_id)["status"] == "failed"
    assert not store.drafts(user) and not store.tickets(user)


def test_call_budget_terminates_endless_tool_loop(settings):
    store, user, conversation = initialized(settings)
    provider = ScriptedProvider([ToolCall("search_knowledge", {"query": "VPN"})] * 20)
    run_id = store.create_run(user, conversation, "VPN", "answer")
    AgentService(store, settings, lambda: provider).execute(run_id, user)
    assert store.run(run_id)["result"]["error"] == "model_call_budget_exceeded"


def test_reviewer_rejects_and_cancels_pending_draft(settings):
    class RejectingDemo(DemoProvider):
        def complete(self, messages, tools, timeout=None):
            if tools[0]["function"]["name"] == "submit_review":
                return ModelReply(
                    [ToolCall("submit_review", {"approved": False, "reason": "不充分"})]
                )
            return super().complete(messages, tools, timeout)

    store, user, conversation = initialized(settings)
    run_id = store.create_run(user, conversation, "VPN 故障需要工单", "ticket")
    AgentService(store, settings, RejectingDemo).execute(run_id, user)
    run = store.run(run_id)
    assert run["status"] == "completed", run
    assert run["result"]["status"] == "insufficient_evidence"
    assert store.drafts(user)[0]["status"] == "cancelled"
    with pytest.raises(ValueError):
        store.decide_draft(store.drafts(user)[0]["id"], user, True)


def test_restart_interrupts_run_and_preserves_completed_draft(settings):
    store, user, conversation = initialized(settings)
    completed = store.create_run(user, conversation, "VPN", "ticket")
    AgentService(store, settings).execute(completed, user)
    draft = store.drafts(user)[0]
    interrupted = store.create_run(user, conversation, "再试一次", "answer")
    store.start_run(interrupted)
    store.initialize(settings)
    assert store.run(interrupted)["result"]["error"] == "server_restarted"
    assert store.decide_draft(draft["id"], user, True)["status"] == "open"


def test_only_one_active_run_per_conversation(settings):
    store, user, conversation = initialized(settings)
    store.create_run(user, conversation, "VPN", "answer")
    with pytest.raises(ValueError):
        store.create_run(user, conversation, "VPN", "answer")


def test_concurrent_approval_creates_exactly_one_ticket(settings):
    store, user, conversation = initialized(settings)
    run_id = store.create_run(user, conversation, "VPN", "ticket")
    AgentService(store, settings).execute(run_id, user)
    draft_id = store.drafts(user)[0]["id"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: store.decide_draft(draft_id, user, True), range(2)))
    assert results[0]["id"] == results[1]["id"]
    assert len(store.tickets(user)) == 1


def live_settings(**kwargs):
    return Settings(mode="live", api_key="test-secret-never-log", model="test-model", **kwargs)


def test_real_adapter_serializes_tools_and_parses_usage():
    received = []

    def handle(request):
        received.append(json.loads(request.content))
        assert request.headers["Authorization"] == "Bearer test-secret-never-log"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "call_a",
                                    "function": {
                                        "name": "search_knowledge",
                                        "arguments": '{"query":"VPN"}',
                                    },
                                }
                            ]
                        }
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 5},
            },
        )

    provider = CompatibleProvider(live_settings(), httpx.MockTransport(handle))
    reply = provider.complete([{"role": "user", "content": "VPN"}], [])
    assert reply.calls[0].arguments == {"query": "VPN"}
    assert reply.input_tokens == 12 and reply.output_tokens == 5
    assert received[0]["tool_choice"] == "required"
    assert received[0]["max_completion_tokens"] == 1500


def test_adapter_transient_retry_and_auth_no_retry():
    statuses = iter([429, 200])

    def transient(request):
        status = next(statuses)
        return httpx.Response(
            status,
            json={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "1",
                                    "function": {
                                        "name": "search_knowledge",
                                        "arguments": '{"query":"VPN"}',
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
        )

    assert (
        CompatibleProvider(live_settings(), httpx.MockTransport(transient)).complete([], []).calls
    )
    calls = []

    def unauthorized(request):
        calls.append(1)
        return httpx.Response(401, text="secret raw provider body")

    with pytest.raises(ProviderError, match="model_http_401") as error:
        CompatibleProvider(live_settings(), httpx.MockTransport(unauthorized)).complete([], [])
    assert len(calls) == 1 and "secret" not in str(error.value)


def test_adapter_timeout_and_invalid_protocol():
    def timeout(request):
        raise httpx.ReadTimeout("provider stalled")

    with pytest.raises(ProviderError, match="timeout_or_network"):
        CompatibleProvider(live_settings(), httpx.MockTransport(timeout)).complete([], [])
    with pytest.raises(ProviderError, match="protocol"):
        CompatibleProvider(
            live_settings(),
            httpx.MockTransport(lambda request: httpx.Response(200, json={"wrong": "format"})),
        ).complete([], [])


def test_chunk_overlap_and_permission_first_retrieval(settings):
    store, user, _ = initialized(settings)
    document = {"id": "long", "version": 2, "title": "x", "content": "a" * 1300}
    chunks = list(split_document(document))
    assert chunks[1]["offset"] == 520
    assert chunks[0]["text"][-80:] == chunks[1]["text"][:80]
    assert not Retriever(store).search("生产密钥轮换", user)
    assert all(
        hit["document_id"] != "BETA-001" for hit in Retriever(store).search("客户名单", user)
    )


def test_live_configuration_and_non_demo_bootstrap(tmp_path):
    with pytest.raises(ValueError, match="requires"):
        Settings(mode="live")
    settings = Settings(
        db_path=str(tmp_path / "prod.db"),
        seed_demo=False,
        admin_username="owner",
        admin_password="unique-long-password",
    )
    store = Store(settings.db_path)
    store.initialize(settings)
    assert store.user_by_name("employee") is None
    assert store.user_by_name("owner")["role"] == "it_admin"
    assert not store.documents(store.user_by_name("owner"))


def test_all_tutorials_are_served_and_path_is_allowlisted(client):
    chapters = client.get("/api/tutorials").json()
    assert len(chapters) == 16
    for chapter in chapters:
        response = client.get("/api/tutorials/" + chapter["slug"])
        assert response.status_code == 200
        assert len(response.json()["content"]) > 500
    assert client.get("/api/tutorials/not-allowed").status_code == 404


def test_live_adapter_through_full_agent_and_reviewer(tmp_path):
    settings = live_settings(db_path=str(tmp_path / "live.db"), seed_demo=True)

    def handle(request):
        body = json.loads(request.content)
        if body["tools"][0]["function"]["name"] == "submit_review":
            call = ToolCall("submit_review", {"approved": True, "reason": "引文直接支持回答"})
        else:
            tool_outputs = [
                json.loads(message["content"])
                for message in body["messages"]
                if message["role"] == "tool"
            ]
            if not tool_outputs:
                call = ToolCall("search_knowledge", {"query": "VPN"})
            else:
                hit = tool_outputs[-1]["hits"][0]
                call = ToolCall(
                    "finish_answer",
                    {
                        "status": "answered",
                        "text": "先检查本地网络。",
                        "citations": [{"chunk_id": hit["chunk_id"], "quote": "先检查本地网络"}],
                    },
                )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"tool_calls": [call.as_message()]}}],
                "usage": {"prompt_tokens": 30, "completion_tokens": 10},
            },
        )

    app = create_app(settings, lambda: CompatibleProvider(settings, httpx.MockTransport(handle)))
    with TestClient(app) as client:
        login(client)
        run, _ = ask(client)
        assert run["result"]["mode"] == "live"
        assert run["result"]["status"] == "answered", run
        assert run["result"]["usage"]["calls"] == 3


def test_global_and_personal_capacity_are_bounded(settings):
    store, user, _ = initialized(settings)
    for index in range(3):
        chat = store.create_conversation(user, str(index))
        store.create_run(user, chat["id"], "VPN", "answer")
    chat = store.create_conversation(user, "overflow")
    with pytest.raises(ValueError, match="capacity"):
        store.create_run(user, chat["id"], "VPN", "answer")


def test_full_application_acceptance():
    from agent_workbench.acceptance import evaluate_application

    report = evaluate_application()
    assert report["total"] == 20
    assert report["passed"] == report["total"], report

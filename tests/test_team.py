"""Behavioral acceptance for the local multi-model workspace."""

import asyncio
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_workbench.team.api import create_team_app
from agent_workbench.team.contracts import ControlInput, MemberInput
from agent_workbench.team.db import TeamStore
from agent_workbench.team.extensions import mcp_operation, validate_extension
from agent_workbench.team.model import ChatModel, ModelFailure
from agent_workbench.team.workspace import Workspace, run_command, safe_path


class MemoryCredentials:
    def __init__(self):
        self.values = {}

    def save(self, id, secret):
        if secret:
            self.values[id] = secret

    def get(self, member):
        return self.values.get(member["id"], "")

    def public(self, member):
        return member | {"key_configured": bool(self.get(member))}


class ScriptModel:
    def __init__(self, steps):
        self.steps = list(steps)
        self.requests = []

    def complete(self, member, key, messages, tools):
        self.requests.append((member, messages, tools))
        name, arguments = self.steps.pop(0)
        call_id = "call_" + str(len(self.requests))
        return {
            "name": name,
            "arguments": arguments,
            "call_id": call_id,
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(arguments)},
                    }
                ],
            },
            "input_tokens": 100,
            "output_tokens": 50,
            "known": True,
        }


@pytest.fixture
def team(tmp_path, monkeypatch):
    monkeypatch.delenv("LLM_MODEL", raising=False)
    app = create_team_app(tmp_path / "app", credentials=MemoryCredentials(), start_worker=False)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        bootstrap = client.get("/api/bootstrap").json()
        client.headers["x-aw-token"] = bootstrap["token"]
        yield client, app.state.store, app.state.runtime, bootstrap


def add_live(client, **kwargs):
    response = client.post(
        "/api/members",
        json={
            "name": "开发员",
            "model": "mock-tools",
            "api_key": "test-secret-not-persisted",
            **kwargs,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def task_for(team, **kwargs):
    client, store, runtime, bootstrap = team
    body = {"title": "实现一个函数", "instruction": "创建函数并验证", **kwargs}
    response = client.post(f"/api/projects/{bootstrap['projects'][0]['id']}/tasks", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def execute(runtime, store, id):
    store.update(id, lambda t: t.update(status="running"))
    runtime.execute(id)
    return store.get(id, "task")


def test_no_login_bootstrap_and_browser_boundary(team):
    client, _, _, bootstrap = team
    assert client.get("/").status_code == 200
    assert client.get("/api/health").json()["login_required"] is False
    assert "登录" not in client.get("/").text.replace("无需登录", "")
    assert (
        client.post(
            "/api/projects", headers={"x-aw-token": "wrong"}, json={"name": "X"}
        ).status_code
        == 403
    )
    assert (
        client.get("/api/bootstrap", headers={"Origin": "https://evil.example"}).status_code == 403
    )
    assert client.get("/api/bootstrap", headers={"Host": "evil.example"}).status_code == 403
    assert client.get("/api/bootstrap", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert bootstrap["projects"] and len(bootstrap["members"]) == 2


def test_all_current_tutorials_are_accessible(team):
    client, _, _, _ = team
    chapters = client.get("/api/tutorials").json()
    assert len(chapters) == 18
    for chapter in chapters:
        response = client.get("/api/tutorials/" + chapter["slug"])
        assert response.status_code == 200
        assert chapter["title"] in response.json()["content"]
        assert len(response.json()["content"]) > 500
    assert client.get("/api/tutorials/not-a-chapter").status_code == 404


def test_credentials_are_referenced_and_never_returned(team):
    client, store, _, _ = team
    member = add_live(client)
    assert member["key_configured"]
    assert "api_key" not in member
    assert "test-secret-not-persisted" not in client.get("/api/bootstrap").text
    assert b"test-secret-not-persisted" not in store.path.read_bytes()
    update = client.put(
        f"/api/members/{member['id']}",
        json={"name": "开发员", "model": "mock-tools", "api_key": ""},
    )
    assert update.json()["key_configured"]


def test_validation_does_not_echo_secret(team):
    client, _, _, _ = team
    secret = "submitted-sensitive-value" * 60
    response = client.post("/api/members", json={"name": "A", "api_key": secret})
    assert response.status_code == 422
    assert "submitted-sensitive-value" not in response.text


def test_case_insensitive_secret_file_boundaries(tmp_path):
    from agent_workbench.team.workspace import file_map

    (tmp_path / ".ENV").write_text("TEST_SECRET=private", encoding="utf-8")
    (tmp_path / "IDENTITY.PEM").write_text("private key", encoding="utf-8")
    (tmp_path / "README.md").write_text("ok", encoding="utf-8")
    assert list(file_map(tmp_path)) == ["README.md"]
    with pytest.raises(ValueError):
        safe_path(tmp_path, ".ENV")


def test_completed_deliverable_cannot_be_reset_by_retry(team):
    _, store, runtime, _ = team
    task = task_for(team)
    completed = execute(runtime, store, task["id"])
    with pytest.raises(ValueError, match="保留"):
        runtime.control(task["id"], ControlInput(action="retry"))
    assert store.get(task["id"], "task")["changes"] == completed["changes"]


def test_project_scopes_members_and_shared_chat(team):
    client, store, runtime, bootstrap = team
    member = add_live(client)
    other = add_live(client, name="另一个项目成员")
    project = bootstrap["projects"][0]
    response = client.put(
        f"/api/projects/{project['id']}",
        json={"name": project["name"], "member_ids": [member["id"]]},
    )
    assert response.status_code == 200
    assert runtime.choose("auto", "balanced", project["id"])[0]["id"] == member["id"]
    assert (
        client.post(
            f"/api/projects/{project['id']}/tasks",
            json={"title": "X", "instruction": "X", "member_id": other["id"]},
        ).status_code
        == 409
    )
    client.post(
        f"/api/projects/{project['id']}/messages",
        json={"content": "页面采用深绿色主题", "action": "message"},
    )
    model = ScriptModel([("finish_task", {"summary": "收到", "verification": "没有写文件"})])
    runtime.model = model
    task = task_for(team)
    assert execute(runtime, store, task["id"])["status"] == "completed"
    assert "页面采用深绿色主题" in json.dumps(model.requests[0][1], ensure_ascii=False)


def test_resume_preserves_partial_files_without_replaying_command(team):
    client, store, runtime, _ = team
    member = add_live(client, max_steps=1)
    runtime.model = ScriptModel(
        [("write_file", {"path": "partial.py", "content": "def answer():\n    return 42\n"})]
    )
    task = task_for(team, member_id=member["id"])
    failed = execute(runtime, store, task["id"])
    assert failed["status"] == "failed"
    runtime.model = ScriptModel(
        [("finish_task", {"summary": "已接手", "verification": "尚未运行测试"})]
    )
    resumed = runtime.control(task["id"], ControlInput(action="resume"))
    done = execute(runtime, store, resumed["id"])
    assert done["status"] == "completed"
    assert (
        Path(done["workspace"], "work/partial.py").read_text(encoding="utf-8")
        == "def answer():\n    return 42\n"
    )
    assert store.get(task["id"], "task")["status"] == "failed"
    assert len(runtime.model.requests) == 1


def test_plan_acceptance_rolls_back_all_children(team):
    import sqlite3

    _, store, runtime, _ = team
    task = task_for(team, kind="plan")
    parent = execute(runtime, store, task["id"])
    before = len(store.list("task"))
    child = dict(parent, id="same-id", plan_accepted=False)
    with pytest.raises(sqlite3.IntegrityError):
        store.accept_plan(parent["id"], [child, child])
    assert len(store.list("task")) == before
    assert store.get(parent["id"], "task")["plan_accepted"] is False


def test_plan_honors_member_tool_capabilities(team):
    client, _, runtime, _ = team
    member = add_live(client, capabilities=[])
    task = task_for(team, member_id=member["id"], kind="plan")
    assert [t["function"]["name"] for t in runtime.allowed_tools(task, member)] == ["submit_plan"]


def test_cancelled_task_cannot_be_marked_complete(team):
    _, store, runtime, _ = team
    task = task_for(team)
    task = execute(runtime, store, task["id"])
    store.update(task["id"], lambda t: t.update(cancel=True, status="cancelled"))
    runtime.finish(task, {"summary": "too late"})
    assert store.get(task["id"], "task")["status"] == "cancelled"


def test_mcp_http_actual_roundtrip():
    import socket
    import threading

    import uvicorn
    from mcp.server import MCPServer

    mcp = MCPServer("http-fixture")

    @mcp.tool()
    def multiply(a: int, b: int) -> int:
        return a * b

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    config = {"transport": "http", "url": f"http://127.0.0.1:{sock.getsockname()[1]}/mcp"}
    server = uvicorn.Server(uvicorn.Config(mcp.streamable_http_app(), log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        assert server.started
        catalog = asyncio.run(mcp_operation(config))
        assert catalog[0]["name"] == "multiply"
        result = asyncio.run(mcp_operation(config, "multiply", {"a": 6, "b": 7}))
        assert "42" in json.dumps(result)
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()
    assert not thread.is_alive()


def test_browser_tool_real_interaction_and_screenshot(tmp_path):
    from agent_workbench.team.workspace import browser_check

    page = tmp_path / "index.html"
    page.write_text(
        "<html><title>验证页</title><button onclick=\"this.textContent='操作成功'\">运行</button></html>",
        encoding="utf-8",
    )
    result = browser_check(
        tmp_path,
        "index.html",
        [{"action": "click", "role": "button", "name": "运行"}],
        tmp_path / "shots",
    )
    assert result["title"] == "验证页"
    assert "操作成功" in result["text"]
    assert result["errors"] == []
    assert (tmp_path / "shots/browser.png").stat().st_size > 1000


def test_demo_is_real_file_flow_and_handoff(team):
    client, store, runtime, bootstrap = team
    task = task_for(team)
    done = execute(runtime, store, task["id"])
    assert done["status"] == "completed"
    assert done["changes"][0]["path"] == "demo/index.html"
    assert not Path(bootstrap["projects"][0]["root"], "demo/index.html").exists()
    assert store.usage(task["project_id"])["calls"] == 0
    child = runtime.control(
        task["id"],
        ControlInput(action="handoff", member_id=bootstrap["members"][1]["id"], note="写交接"),
    )
    done_child = execute(runtime, store, child["id"])
    assert done_child["status"] == "completed"
    assert {c["path"] for c in done_child["changes"]} == {"demo/index.html", "DEMO-HANDOFF.md"}
    runtime.control(child["id"], ControlInput(action="apply"))
    assert Path(bootstrap["projects"][0]["root"], "DEMO-HANDOFF.md").exists()
    runtime.control(
        task["id"], ControlInput(action="apply")
    )  # already integrated dependency is idempotent
    assert (
        client.get(f"/preview/{task['id']}/demo/index.html")
        .headers["content-security-policy"]
        .startswith("sandbox")
    )


def test_live_files_commands_approval_and_handoff_evidence(team):
    client, store, runtime, _ = team
    member = add_live(client)
    runtime.model = ScriptModel(
        [
            ("write_file", {"path": "hello.py", "content": "print(42)\n"}),
            ("run_command", {"argv": ["python", "hello.py"], "timeout": 10}),
            (
                "finish_task",
                {
                    "summary": "程序已完成",
                    "verification": "运行 hello.py 得到 42",
                    "open_issues": "",
                },
            ),
        ]
    )
    task = task_for(team, member_id=member["id"])
    paused = execute(runtime, store, task["id"])
    assert paused["status"] == "waiting_approval"
    assert len(runtime.model.requests) == 2
    runtime.control(task["id"], ControlInput(action="approve"))
    with pytest.raises(ValueError):
        runtime.control(task["id"], ControlInput(action="approve"))
    done = execute(runtime, store, task["id"])
    assert done["status"] == "completed"
    outputs = [
        e["payload"]["output"] for e in store.events(task["project_id"]) if e["kind"] == "tool"
    ]
    assert any('"exit_code": 0' in output and "42" in output for output in outputs)
    assert store.usage(task["project_id"])["tokens"] == 450


def test_rejection_does_not_run_command(team):
    client, store, runtime, _ = team
    member = add_live(client)
    runtime.model = ScriptModel(
        [
            ("run_command", {"argv": ["python", "-c", "open('bad.txt','w').write('x')"]}),
            ("finish_task", {"summary": "操作被拒绝，未执行", "verification": "未执行"}),
        ]
    )
    task = task_for(team, member_id=member["id"])
    execute(runtime, store, task["id"])
    runtime.control(task["id"], ControlInput(action="reject"))
    done = execute(runtime, store, task["id"])
    assert done["status"] == "completed"
    assert not Path(done["workspace"], "work/bad.txt").exists()


@pytest.mark.parametrize(
    "path",
    [
        "../secret.txt",
        ".env",
        "sub/.env.production",
        ".git/config",
        "C:/Windows/x",
        ".ssh/key",
        "secret.pem",
    ],
)
def test_file_boundary_rejects_paths(tmp_path, path):
    with pytest.raises(ValueError):
        safe_path(tmp_path, path)


def test_copy_excludes_credentials_and_detects_conflict(team):
    client, store, runtime, bootstrap = team
    root = Path(bootstrap["projects"][0]["root"])
    (root / ".env").write_text("SECRET=actual-key")
    member = add_live(client)
    (root / "module.py").write_text("original")
    runtime.model = ScriptModel(
        [
            ("write_file", {"path": "module.py", "content": "agent change"}),
            ("finish_task", {"summary": "完成"}),
        ]
    )
    task = task_for(team, member_id=member["id"])
    done = execute(runtime, store, task["id"])
    assert not Path(done["workspace"], "work/.env").exists()
    (root / "module.py").write_text("user change")
    with pytest.raises(ValueError, match="拒绝覆盖"):
        runtime.control(task["id"], ControlInput(action="apply"))
    assert (root / "module.py").read_text() == "user change"


def test_model_cannot_call_removed_capability(team):
    client, store, runtime, _ = team
    member = add_live(client, capabilities=["files"])
    runtime.model = ScriptModel(
        [
            ("run_command", {"argv": ["python", "-c", "print('no')"]}),
            ("finish_task", {"summary": "没有权限"}),
        ]
    )
    task = task_for(team, member_id=member["id"])
    done = execute(runtime, store, task["id"])
    assert done["status"] == "completed"
    assert any("未授权" in e["payload"].get("output", "") for e in store.events(task["project_id"]))


def test_budget_reservation_is_atomic_and_blocks_unknown_prices(team):
    client, store, _, bootstrap = team
    member = add_live(client)
    project = bootstrap["projects"][0]
    store.update(project["id"], lambda p: p.update(token_budget=1000))

    def reserve(_):
        try:
            store.reserve(project["id"], "test", member, 600, None)
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(reserve, [1, 2])) == 1
    store.update(project["id"], lambda p: p.update(budget_usd=1, token_budget=10000))
    with pytest.raises(ValueError, match="单价"):
        store.reserve(project["id"], "test", member, 100, None)


def test_budget_failure_never_calls_model(team):
    client, store, runtime, bootstrap = team
    member = add_live(client)
    store.update(bootstrap["projects"][0]["id"], lambda p: p.update(token_budget=1000))
    runtime.model = ScriptModel([])
    task = task_for(team, member_id=member["id"])
    done = execute(runtime, store, task["id"])
    assert done["status"] == "failed" and "预算" in done["error"]
    assert runtime.model.requests == []


def test_plan_confirm_is_explicit_and_duplicate_safe(team):
    _, store, runtime, _ = team
    task = task_for(team, kind="plan")
    done = execute(runtime, store, task["id"])
    assert done["result"]["plan"]
    assert len(store.list("task")) == 1
    runtime.control(task["id"], ControlInput(action="accept_plan"))
    assert len(store.list("task")) == 3
    assert all(t["status"] == "draft" for t in store.list("task")[1:])
    with pytest.raises(ValueError):
        runtime.control(task["id"], ControlInput(action="accept_plan"))


def test_plan_cannot_create_dependency_cycle(team):
    from agent_workbench.team.contracts import Plan

    _, _, runtime, bootstrap = team
    plan = Plan(
        summary="bad",
        tasks=[
            {
                "title": "task",
                "instruction": "work",
                "member_id": bootstrap["members"][0]["id"],
                "depends_on": [0],
            }
        ],
    )
    with pytest.raises(ValueError):
        runtime.validate_plan(plan)


def test_auto_routing_prefers_live_and_respects_manual(team):
    client, _, runtime, bootstrap = team
    expert = add_live(client, tier="expert")
    economy = add_live(client, name="经济成员", tier="economy")
    assert runtime.choose("auto", "economy")[0]["id"] == economy["id"]
    assert runtime.choose("auto", "expert")[0]["id"] == expert["id"]
    assert runtime.choose(bootstrap["members"][0]["id"], "expert")[0]["provider"] == "demo"


def test_chat_only_message_does_not_wake_every_member(team):
    client, store, _, bootstrap = team
    pid = bootstrap["projects"][0]["id"]
    assert (
        client.post(
            f"/api/projects/{pid}/messages", json={"content": "项目约定", "action": "message"}
        ).status_code
        == 201
    )
    assert store.list("task") == []
    response = client.post(
        f"/api/projects/{pid}/messages",
        json={"content": "@演示开发员 做一个示例", "action": "message"},
    )
    assert response.json()["task"]["member_id"] == bootstrap["members"][0]["id"]


def test_restart_marks_unsafe_work_interrupted_and_preserves_completed(team):
    _, store, runtime, _ = team
    done = task_for(team)
    execute(runtime, store, done["id"])
    active = task_for(team)
    store.update(active["id"], lambda t: t.update(status="running"))
    store.initialize()
    assert store.get(done["id"])["status"] == "completed"
    assert store.get(active["id"])["status"] == "interrupted"


def test_scheduler_runs_dependencies_and_cancel_draft(team):
    _, store, runtime, bootstrap = team
    first = task_for(team, start=True, member_id=bootstrap["members"][0]["id"])
    second = task_for(
        team, start=True, member_id=bootstrap["members"][1]["id"], dependencies=[first["id"]]
    )
    cancelled = task_for(team)
    runtime.control(cancelled["id"], ControlInput(action="cancel"))
    runtime.start()
    deadline = time.monotonic() + 10
    while store.get(second["id"])["status"] != "completed" and time.monotonic() < deadline:
        time.sleep(0.1)
    assert store.get(second["id"])["status"] == "completed"
    assert store.get(cancelled["id"])["status"] == "cancelled"


def test_command_timeout_and_no_model_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "private-value")
    result = run_command(
        tmp_path, ["python", "-c", "import os; print(os.getenv('LLM_API_KEY','absent'))"], 5
    )
    assert result["exit_code"] == 0 and "absent" in result["output"]
    result = run_command(tmp_path, ["python", "-c", "import time; time.sleep(10)"], 1)
    assert result["status"] == "timeout"


def test_plugin_exec_and_skill_disclosure(team):
    client, store, runtime, bootstrap = team
    path = (
        Path(__file__).resolve().parents[1]
        / "agent_workbench/team_assets/plugins/project-inspector/plugin.json"
    )
    response = client.post(
        "/api/extensions",
        json={"name": "统计", "kind": "plugin", "enabled": True, "config": {"path": str(path)}},
    )
    assert response.status_code == 201
    id = response.json()["id"]
    assert client.post(f"/api/extensions/{id}/inspect").status_code == 200
    result = runtime.extensions.call(
        id, "count_source_files", {}, Path(bootstrap["projects"][0]["root"]), lambda: False
    )
    assert result["exit_code"] == 0
    skill = store.list("extension")[0]
    assert "软件交付流程" in runtime.extensions.skill(skill["id"], "SKILL.md")["content"]
    with pytest.raises(ValueError):
        runtime.extensions.skill(skill["id"], "../outside.md")
    assert "content" not in runtime.extensions.catalog()[0]


def test_mcp_stdio_actual_roundtrip(tmp_path):
    server = tmp_path / "server.py"
    server.write_text(
        'from mcp.server import MCPServer\nmcp=MCPServer("fixture")\n@mcp.tool()\ndef add(a:int,b:int)->int:\n    return a+b\nif __name__=="__main__":\n    mcp.run()\n',
        encoding="utf-8",
    )
    config = validate_extension(
        "mcp", {"transport": "stdio", "command": sys.executable, "args": [str(server)]}
    )
    catalog = asyncio.run(mcp_operation(config))
    assert catalog[0]["name"] == "add"
    result = asyncio.run(mcp_operation(config, "add", {"a": 2, "b": 3}))
    assert "5" in json.dumps(result)


def test_compatible_model_retains_reasoning_protocol():
    def handler(request):
        body = json.loads(request.content)
        assert body["model"] == "test-model" and body["max_tokens"] == 4096
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "reasoning_content": "provider continuation",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "abc",
                                    "type": "function",
                                    "function": {
                                        "name": "finish_task",
                                        "arguments": '{"summary":"ok"}',
                                    },
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 3},
            },
        )

    model = ChatModel(httpx.MockTransport(handler))
    member = MemberInput(
        name="test", model="test-model", base_url="https://example.com/v1"
    ).model_dump()
    result = model.complete(member, "key", [], [])
    assert result["known"] and result["message"]["reasoning_content"] == "provider continuation"


@pytest.mark.parametrize("code", [401, 429, 500])
def test_compatible_model_errors_do_not_retry_or_expose_body(code):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(code, text="secret-echo")

    model = ChatModel(httpx.MockTransport(handler))
    member = MemberInput(name="test", model="test").model_dump()
    with pytest.raises(ModelFailure) as error:
        model.complete(member, "private", [], [])
    assert "secret-echo" not in str(error.value) and len(calls) == 1


def test_workspace_refuses_changed_deliverable(team):
    _, store, runtime, bootstrap = team
    task = task_for(team)
    done = execute(runtime, store, task["id"])
    Path(done["workspace"], "work/demo/index.html").write_text("changed later")
    with pytest.raises(ValueError, match="完成后发生变化"):
        Workspace(store.path.parent).apply(done, bootstrap["projects"][0])


def test_legacy_database_is_not_modified(tmp_path):
    legacy = tmp_path / "agent_workbench.db"
    legacy.write_bytes(b"old-data")
    TeamStore(tmp_path / "team/team.db").initialize()
    assert legacy.read_bytes() == b"old-data"


def test_incomplete_model_usage_is_marked_estimated(team):
    client, store, _, bootstrap = team
    member = add_live(client, input_price=1, output_price=2)
    id = store.reserve(bootstrap["projects"][0]["id"], "task", member, 1000, 0.002)
    store.settle(id, 0, 0, member, False)
    report = store.usage(bootstrap["projects"][0]["id"])
    assert report["tokens"] == 1000 and report["estimated"] and report["cost"] == 0.002

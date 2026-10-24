"""Skills, MCP and declarative tool plugins; explicit activation, bounded outputs."""

import asyncio
import json
import os
from contextlib import AsyncExitStack
from pathlib import Path
from urllib.parse import urlsplit

import jsonschema
import yaml

from .workspace import clean_env, run_command, safe_path


def load_skill(root):
    root = Path(root).expanduser().resolve()
    text = (root / "SKILL.md").read_text(encoding="utf-8")
    if len(text) > 40000 or not text.startswith("---"):
        raise ValueError("技能需要带 YAML 元数据的 SKILL.md，且小于 40KB")
    parts = text.split("---", 2)
    if len(parts) != 3:
        raise ValueError("SKILL.md 元数据格式错误")
    meta = yaml.safe_load(parts[1])
    if not isinstance(meta, dict) or not meta.get("name") or not meta.get("description"):
        raise ValueError("技能需要 name 和 description")
    return {
        "path": str(root),
        "name": str(meta["name"])[:100],
        "description": str(meta["description"])[:1000],
    }


def load_plugin(path):
    path = Path(path).expanduser().resolve()
    if path.stat().st_size > 100000:
        raise ValueError("插件清单过大")
    data = json.loads(path.read_text(encoding="utf-8"))
    if (
        data.get("schema_version") != 1
        or not isinstance(data.get("tools"), list)
        or not 1 <= len(data["tools"]) <= 20
    ):
        raise ValueError("插件需 schema_version=1 和 1–20 个 tools")
    names = set()
    for tool in data["tools"]:
        if (
            not isinstance(tool.get("name"), str)
            or tool["name"] in names
            or not tool.get("description")
        ):
            raise ValueError("插件工具名称必须唯一并提供 description")
        names.add(tool["name"])
        if (
            not isinstance(tool.get("argv"), list)
            or not tool["argv"]
            or not all(isinstance(x, str) for x in tool["argv"])
        ):
            raise ValueError("插件工具需要 argv 数组")
        jsonschema.Draft202012Validator.check_schema(tool.get("input_schema", {"type": "object"}))
    return {"path": str(path), "manifest": data}


def validate_extension(kind, config):
    if kind == "skill":
        return load_skill(config["path"])
    if kind == "plugin":
        return load_plugin(config["path"])
    transport = config.get("transport", "stdio")
    if transport not in {"stdio", "http"}:
        raise ValueError("MCP transport 仅支持 stdio 或 http")
    if transport == "stdio":
        if not isinstance(config.get("command"), str) or not config["command"]:
            raise ValueError("stdio MCP 需要 command")
        if not isinstance(config.get("args", []), list) or not all(
            isinstance(a, str) for a in config.get("args", [])
        ):
            raise ValueError("args 必须为字符串数组")
    else:
        from .contracts import MemberInput

        MemberInput.endpoint(config.get("url", ""))
    env = config.get("env_refs", {})
    if not isinstance(env, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in env.items()
    ):
        raise ValueError("env_refs 使用变量名映射，不保存明文凭据")
    allowed = {"transport", "command", "args", "url", "env_refs", "bearer_env"}
    if set(config) - allowed:
        raise ValueError("未知 MCP 配置项；密钥只能使用环境变量引用")
    return config | {"transport": transport}


async def mcp_operation(config, tool=None, arguments=None):
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters
    from mcp.client.streamable_http import streamable_http_client

    async with AsyncExitStack() as stack, asyncio.timeout(45):
        if config["transport"] == "stdio":
            env = clean_env() | {k: os.getenv(v, "") for k, v in config.get("env_refs", {}).items()}
            transport = StdioServerParameters(
                command=config["command"], args=config.get("args", []), env=env
            )
        else:
            import httpx2

            headers = {}
            if config.get("bearer_env"):
                headers["Authorization"] = "Bearer " + os.getenv(config["bearer_env"], "")
            client_http = await stack.enter_async_context(
                httpx2.AsyncClient(
                    headers=headers,
                    follow_redirects=False,
                    trust_env=urlsplit(config["url"]).hostname
                    not in {"127.0.0.1", "localhost", "::1"},
                )
            )
            transport = streamable_http_client(config["url"], http_client=client_http)
        async with Client(transport) as client:
            if tool is None:
                response = await client.list_tools()
                tools = response.tools if hasattr(response, "tools") else response
                return [
                    {
                        "name": t.name,
                        "description": (t.description or "")[:1000],
                        "input_schema": t.input_schema
                        if hasattr(t, "input_schema")
                        else t.inputSchema,
                    }
                    for t in tools
                ][:100]
            response = await client.call_tool(tool, arguments or {})
            return response.model_dump(mode="json", by_alias=True)


class Extensions:
    def __init__(self, store):
        self.store = store

    def catalog(self):
        return [
            {
                "id": e["id"],
                "name": e["name"],
                "kind": e["kind"],
                "description": e["config"].get("description", ""),
                "tools": e.get("tools", []),
            }
            for e in self.store.list("extension")
            if e["enabled"]
        ]

    def skill(self, id, resource):
        e = self.store.get(id, "extension")
        if not e["enabled"] or e["kind"] != "skill":
            raise ValueError("技能未启用")
        path = safe_path(e["config"]["path"], resource)
        if path.stat().st_size > 40000:
            raise ValueError("技能资源过大，请拆分资源")
        return {
            "skill": e["name"],
            "resource": resource,
            "content": path.read_text(encoding="utf-8"),
        }

    def inspect(self, e):
        if e["kind"] == "mcp":
            tools = asyncio.run(mcp_operation(e["config"]))
        elif e["kind"] == "plugin":
            # Re-read only on explicit inspect, pin snapshot so later file edits don't silently change commands.
            config = load_plugin(e["config"]["path"])
            self.store.update(e["id"], lambda x: x.update(config=config))
            tools = [
                {k: t[k] for k in ("name", "description", "input_schema") if k in t}
                for t in config["manifest"]["tools"]
            ]
        else:
            self.skill(e["id"], "SKILL.md")
            tools = []
        return self.store.update(e["id"], lambda x: x.update(tools=tools, error="", tested=True))

    def call(self, id, tool, arguments, root, cancelled):
        e = self.store.get(id, "extension")
        if not e["enabled"] or e["kind"] == "skill":
            raise ValueError("扩展未启用或不提供工具")
        listed = next((t for t in e.get("tools", []) if t["name"] == tool), None)
        if listed is None:
            raise ValueError("工具未经过连接检查，请在能力中心检查连接")
        jsonschema.validate(arguments, listed.get("input_schema", {"type": "object"}))
        if e["kind"] == "mcp":
            return asyncio.run(mcp_operation(e["config"], tool, arguments))
        definition = next(t for t in e["config"]["manifest"]["tools"] if t["name"] == tool)
        # Values are single argv elements, never interpolated into a shell string.
        argv = []
        for part in definition["argv"]:
            if part == "{workspace}":
                argv.append(str(root))
            elif part == "{plugin_dir}":
                argv.append(str(Path(e["config"]["path"]).parent))
            elif part.startswith("{arg:") and part.endswith("}"):
                argv.append(str(arguments[part[5:-1]]))
            else:
                argv.append(part.replace("{plugin_dir}", str(Path(e["config"]["path"]).parent)))
        return run_command(root, argv, min(int(definition.get("timeout", 60)), 120), cancelled)

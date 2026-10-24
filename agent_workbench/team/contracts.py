"""Untrusted HTTP/model inputs meet the same explicit contracts."""

from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProjectInput(Input):
    name: str = Field(min_length=1, max_length=80)
    root: str = Field(default="", max_length=1000)
    brief: str = Field(default="", max_length=12000)
    token_budget: int = Field(default=200000, ge=1000, le=100000000)
    budget_usd: float | None = Field(default=None, gt=0, le=10000, allow_inf_nan=False)
    member_ids: list[str] = Field(default_factory=list, max_length=30)


class MemberInput(Input):
    name: str = Field(min_length=1, max_length=50)
    role: str = Field(default="软件开发", max_length=500)
    provider: Literal["demo", "compatible"] = "compatible"
    base_url: str = "https://api.openai.com/v1"
    model: str = Field(default="", max_length=150)
    api_key: str = Field(default="", max_length=1000, repr=False)
    key_env: str = Field(default="", pattern=r"^([A-Z][A-Z0-9_]{0,79})?$")
    tier: Literal["economy", "balanced", "expert"] = "balanced"
    input_price: float | None = Field(default=None, ge=0, le=10000, allow_inf_nan=False)
    output_price: float | None = Field(default=None, ge=0, le=10000, allow_inf_nan=False)
    max_output: int = Field(default=4096, ge=256, le=32000)
    max_steps: int = Field(default=16, ge=1, le=50)
    enabled: bool = True
    capabilities: list[Literal["files", "commands", "browser", "extensions"]] = Field(
        default_factory=lambda: ["files", "commands", "browser", "extensions"]
    )

    @field_validator("base_url")
    @classmethod
    def endpoint(cls, value):
        p = urlsplit(value)
        if not p.hostname or p.username or p.password or p.query or p.fragment:
            raise ValueError("接口地址不能包含凭据、查询参数或片段")
        if p.scheme != "https" and not (
            p.scheme == "http" and p.hostname in {"127.0.0.1", "localhost", "::1"}
        ):
            raise ValueError("接口需要 HTTPS，本机接口可使用 HTTP")
        return value.rstrip("/")


class TaskInput(Input):
    title: str = Field(min_length=1, max_length=150)
    instruction: str = Field(min_length=1, max_length=16000)
    member_id: str = "auto"
    difficulty: Literal["economy", "balanced", "expert"] = "balanced"
    dependencies: list[str] = Field(default_factory=list, max_length=12)
    start: bool = False
    kind: Literal["work", "plan"] = "work"


class MessageInput(Input):
    content: str = Field(min_length=1, max_length=16000)
    member_id: str = ""
    action: Literal["message", "task", "plan"] = "message"


class ControlInput(Input):
    action: Literal[
        "start", "cancel", "retry", "resume", "handoff", "apply", "approve", "reject", "accept_plan"
    ]
    member_id: str = ""
    note: str = Field(default="", max_length=8000)


class ExtensionInput(Input):
    kind: Literal["skill", "mcp", "plugin"]
    name: str = Field(min_length=1, max_length=100)
    config: dict = Field(default_factory=dict)
    enabled: bool = False


class FileInput(Input):
    path: str = Field(min_length=1, max_length=500)


class ReadFile(FileInput):
    start: int = Field(default=1, ge=1)
    lines: int = Field(default=200, ge=1, le=500)


class WriteFile(FileInput):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    content: str = Field(max_length=150000)


class EditFile(FileInput):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    old: str = Field(min_length=1, max_length=50000)
    new: str = Field(max_length=50000)


class Search(Input):
    query: str = Field(min_length=1, max_length=300)


class Command(Input):
    argv: list[str] = Field(min_length=1, max_length=30)
    timeout: int = Field(default=60, ge=1, le=120)


class Browse(Input):
    path: str = Field(default="index.html", max_length=500)
    actions: list[dict] = Field(default_factory=list, max_length=12)


class SkillRead(Input):
    skill_id: str
    resource: str = "SKILL.md"


class ExtensionCall(Input):
    extension_id: str
    tool: str
    arguments: dict = Field(default_factory=dict)


class Finish(Input):
    summary: str = Field(min_length=1, max_length=12000)
    verification: str = Field(default="未执行验证", max_length=5000)
    open_issues: str = Field(default="", max_length=5000)


class PlannedTask(Input):
    title: str = Field(min_length=1, max_length=150)
    instruction: str = Field(min_length=1, max_length=6000)
    member_id: str
    depends_on: list[int] = Field(default_factory=list, max_length=8)


class Plan(Input):
    summary: str = Field(min_length=1, max_length=3000)
    tasks: list[PlannedTask] = Field(min_length=1, max_length=8)


def tool(name, description, model):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": model.model_json_schema(),
        },
    }


TOOL_MODELS = {
    "list_files": Input,
    "read_file": ReadFile,
    "write_file": WriteFile,
    "edit_file": EditFile,
    "search_files": Search,
    "run_command": Command,
    "browser_check": Browse,
    "read_skill": SkillRead,
    "extension_call": ExtensionCall,
    "finish_task": Finish,
    "submit_plan": Plan,
}

DESCRIPTIONS = {
    "list_files": "列出当前任务独立工作副本中的文件。",
    "read_file": "读取工作副本中的 UTF-8 文件，返回行号。",
    "write_file": "在工作副本写入完整 UTF-8 文件，最终由用户合入原项目。",
    "edit_file": "唯一匹配 old 文本后替换为 new，不匹配或多处匹配时失败。",
    "search_files": "在工作副本按字面文本搜索，返回文件和行号。",
    "run_command": "在工作副本执行 argv 命令，需要本次操作的人工授权。python 自动指向当前解释器。",
    "browser_check": "在独立浏览器中验证工作副本的静态网页。可按 role/name click 或 fill，截图并返回页面文本。需要授权。",
    "read_skill": "按需读取已启用技能的 SKILL.md 或相对路径资源。",
    "extension_call": "调用已启用 MCP 或插件工具。外部执行需要本次操作授权。",
    "finish_task": "提交交接报告。verification 必须对应实际工具结果，不得把未运行的检查说成通过。",
    "submit_plan": "提交待用户确认的任务计划。depends_on 使用前面任务的 0 起始序号；member_id 必须来自成员列表。",
}

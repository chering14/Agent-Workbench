"""One model protocol, an explicit rules demo, and real compatible HTTP calls."""

import json
import time
from dataclasses import dataclass, field
from uuid import uuid4

import httpx


class ProviderError(RuntimeError):
    pass


@dataclass
class ToolCall:
    name: str
    arguments: dict
    id: str = field(default_factory=lambda: "call_" + uuid4().hex[:12])

    def as_message(self):
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(self.arguments, ensure_ascii=False),
            },
        }


@dataclass
class ModelReply:
    calls: list[ToolCall]
    input_tokens: int = 0
    output_tokens: int = 0
    usage_known: bool = True


class CompatibleProvider:
    def __init__(self, settings, transport=None):
        self.settings = settings
        self.transport = transport

    def complete(self, messages, tools, timeout=None):
        payload = {
            "model": self.settings.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "required",
            "parallel_tool_calls": False,
        }
        limit_field = (
            "max_completion_tokens" if "api.openai.com" in self.settings.base_url else "max_tokens"
        )
        payload[limit_field] = self.settings.max_output_tokens
        # Only transport failures and transient HTTP statuses receive a bounded retry.
        for attempt in range(2):
            try:
                with httpx.Client(
                    transport=self.transport,
                    timeout=timeout or self.settings.timeout,
                    follow_redirects=False,
                ) as client:
                    response = client.post(
                        self.settings.base_url.rstrip("/") + "/chat/completions",
                        json=payload,
                        headers={"Authorization": "Bearer " + self.settings.api_key},
                    )
                if response.status_code in {429, 500, 502, 503, 504} and attempt == 0:
                    time.sleep(0.15)
                    continue
                if response.status_code >= 400:
                    raise ProviderError(f"model_http_{response.status_code}")
                if len(response.content) > 1_000_000:
                    raise ProviderError("model_response_too_large")
                body = response.json()
                raw_calls = body["choices"][0]["message"].get("tool_calls", [])
                if not raw_calls or len(raw_calls) > 4:
                    raise ProviderError("model_did_not_return_valid_tool_calls")
                calls = []
                for raw in raw_calls:
                    arguments = json.loads(raw["function"]["arguments"])
                    if not isinstance(arguments, dict):
                        raise ValueError("tool arguments must be an object")
                    calls.append(ToolCall(raw["function"]["name"], arguments, raw["id"]))
                usage = body.get("usage") or {}
                known = "prompt_tokens" in usage and "completion_tokens" in usage
                return ModelReply(
                    calls,
                    int(usage.get("prompt_tokens", 0)),
                    int(usage.get("completion_tokens", 0)),
                    known,
                )
            except (httpx.TimeoutException, httpx.NetworkError) as error:
                if attempt == 0:
                    continue
                raise ProviderError("model_timeout_or_network_error") from error
            except (KeyError, ValueError, TypeError, IndexError) as error:
                raise ProviderError("model_protocol_error") from error
        raise ProviderError("model_unavailable")


class DemoProvider:
    """Rules only: exercises the same tool boundary, without claiming LLM reasoning."""

    def complete(self, messages, tools, timeout=None):
        if tools[0]["function"]["name"] == "submit_review":
            return ModelReply(
                [
                    ToolCall(
                        "submit_review",
                        {
                            "approved": True,
                            "reason": "规则演示仅做流程审核；语义核验需要真实模型和人工评测。",
                        },
                    )
                ]
            )
        request = json.loads(
            next(message["content"] for message in reversed(messages) if message["role"] == "user")
        )
        question = request["question"]
        outputs = [
            json.loads(message["content"]) for message in messages if message["role"] == "tool"
        ]
        search = next((output for output in reversed(outputs) if "hits" in output), None)
        draft = next((output.get("draft") for output in outputs if output.get("draft")), None)
        if search is None:
            query = question
            if request["intent"] == "ticket" and request.get("previous_question"):
                query = request["previous_question"]
            return ModelReply([ToolCall("search_knowledge", {"query": query})])
        hits = search["hits"]
        if request["intent"] == "ticket" and not draft:
            description = (
                "用户问题："
                + (request.get("previous_question") or question)
                + "\n补充说明："
                + question
            )
            if hits:
                description += "\n关联资料：" + hits[0]["title"]
            return ModelReply(
                [
                    ToolCall(
                        "prepare_ticket",
                        {
                            "title": "协助处理：" + question[:60],
                            "description": description,
                            "priority": "normal",
                        },
                    )
                ]
            )
        citations = [{"chunk_id": hits[0]["chunk_id"], "quote": hits[0]["text"]}] if hits else []
        if draft:
            status, text = (
                "awaiting_confirmation",
                "工单草稿已准备，请核对下方内容后确认创建。正式工单将在你确认后生成。",
            )
        elif not hits:
            status, text = (
                "insufficient_evidence",
                "当前可访问资料中没有足够依据。请补充具体问题，或选择生成工单草稿交由人工处理。",
            )
        elif any(word in question for word in ("费用", "月费", "多少钱")) and not any(
            word in hits[0]["text"] for word in ("费用", "月费", "元")
        ):
            status, text, citations = (
                "insufficient_evidence",
                "找到了主题相关资料，但其中没有费用信息，无法据此回答金额。",
                [],
            )
        else:
            status, text = (
                "evidence_found",
                "以下是匹配到的资料原文。当前为规则演示模式，请核对是否适用：\n\n"
                + hits[0]["text"],
            )
        return ModelReply(
            [ToolCall("finish_answer", {"status": status, "text": text, "citations": citations})]
        )

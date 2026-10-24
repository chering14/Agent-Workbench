"""A small compatible model adapter; no hidden provider retries or demo fallback."""

import json
from urllib.parse import urlsplit

import httpx


class ModelFailure(RuntimeError):
    pass


class ChatModel:
    def __init__(self, transport=None):
        self.transport = transport

    def complete(self, member, key, messages, tools):
        payload = {
            "model": member["model"],
            "messages": messages,
            "tools": tools,
            "tool_choice": "required",
            "parallel_tool_calls": False,
        }
        limit = (
            "max_completion_tokens"
            if urlsplit(member["base_url"]).hostname == "api.openai.com"
            else "max_tokens"
        )
        payload[limit] = member["max_output"]
        headers = {"Authorization": "Bearer " + key} if key else {}
        try:
            with httpx.Client(
                transport=self.transport,
                timeout=90,
                follow_redirects=False,
                trust_env=urlsplit(member["base_url"]).hostname
                not in {"127.0.0.1", "localhost", "::1"},
            ) as client:
                with client.stream(
                    "POST", member["base_url"] + "/chat/completions", json=payload, headers=headers
                ) as response:
                    if response.status_code != 200:
                        raise ModelFailure(
                            f"模型接口返回 HTTP {response.status_code}；请检查模型、接口和额度"
                        )
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 2_000_000:
                            raise ModelFailure("模型响应超过大小限制")
            body = json.loads(raw)
            message = body["choices"][0]["message"]
            calls = message.get("tool_calls", [])
            if len(calls) != 1 or calls[0].get("type") != "function":
                raise ModelFailure("模型需每步返回一个函数工具调用；该接口未满足当前协议")
            call = calls[0]
            arguments = json.loads(call["function"]["arguments"])
            if not isinstance(arguments, dict) or not isinstance(call["id"], str):
                raise ValueError()
            usage = body.get("usage") or {}
            known = all(
                isinstance(usage.get(k), int) and usage[k] >= 0
                for k in ("prompt_tokens", "completion_tokens")
            )
            # Some compatible reasoning models require reasoning_content to continue tool calls.
            message = {
                k: v
                for k, v in message.items()
                if k in {"role", "content", "tool_calls", "reasoning_content"}
            }
            return {
                "message": message,
                "name": call["function"]["name"],
                "arguments": arguments,
                "call_id": call["id"],
                "input_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0),
                "known": known,
            }
        except (httpx.HTTPError, UnicodeError):
            raise ModelFailure("模型连接中断或超时；未自动重复可能已计费的调用") from None
        except (KeyError, IndexError, TypeError, ValueError):
            raise ModelFailure("模型返回的工具调用格式无效") from None

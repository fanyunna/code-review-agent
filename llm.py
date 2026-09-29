"""DeepSeek LLM 客户端。

直接调用 DeepSeek 的 OpenAI 兼容接口（``/chat/completions``），
使用 ``requests`` 而非任何 Agent 框架，从而把「模型 → 工具调用 → 结果」
这一循环的每一环都显式暴露出来。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import List, Optional

import requests

from config import Config


class LLMError(Exception):
    """LLM 调用失败时抛出。"""


@dataclass
class ToolCall:
    """模型发起的一次工具调用。"""

    id: str
    name: str
    arguments: dict


@dataclass
class AssistantMessage:
    """模型的一轮回复：可能包含正文、工具调用，或两者皆有。"""

    content: Optional[str] = None
    tool_calls: List[ToolCall] = field(default_factory=list)

    def to_dict(self) -> dict:
        """还原为 OpenAI 消息格式，便于拼回 messages 列表继续对话。"""
        msg = {"role": "assistant", "content": self.content or ""}
        if self.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                    },
                }
                for tc in self.tool_calls
            ]
        return msg


class DeepSeekClient:
    """封装 DeepSeek 聊天补全接口，带指数退避重试。"""

    def __init__(self, config: Config):
        self.config = config
        self.session = requests.Session()

    def chat(self, messages: list, tools: Optional[list] = None) -> AssistantMessage:
        """发送一轮对话，返回模型的回复（正文 + 工具调用）。

        :param messages: 完整对话历史（system/user/assistant/tool 消息）
        :param tools: 工具 JSON Schema 列表（OpenAI function-calling 格式）
        """
        url = self.config.base_url + "/chat/completions"
        headers = {
            "Authorization": "Bearer " + self.config.api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        if tools:
            payload["tools"] = tools

        last_error = None
        for attempt in range(3):
            try:
                resp = self.session.post(url, headers=headers, json=payload, timeout=60)
            except requests.RequestException as e:
                last_error = "网络请求失败: %s" % e
            else:
                # 429 / 5xx 属于可重试的瞬时错误
                if resp.status_code in (429, 500, 502, 503, 504):
                    last_error = "服务端临时错误(%d)，将重试" % resp.status_code
                elif resp.status_code != 200:
                    raise LLMError(
                        "DeepSeek API 返回错误 %d: %s" % (resp.status_code, resp.text[:500])
                    )
                else:
                    return self._parse_response(resp.json())
            time.sleep(2 ** attempt)  # 1s / 2s / 4s

        raise LLMError("连续 3 次调用失败，已放弃：%s" % last_error)

    def chat_stream(self, messages: list, tools: Optional[list] = None):
        """流式调用：随生成逐 token 产出回复，返回一个生成器。

        生成器依次产出事件字典：
          {"type": "delta", "content": str}            —— 正文片段（可能多次）
          {"type": "tool_calls", "tool_calls": [...]}  —— 一轮结束时若有工具调用
        若一轮结束且无工具调用，说明正文已通过 delta 事件全部产出完毕。

        使用 DeepSeek 的 SSE 流式接口（``stream: true``），逐行解析
        ``data:`` 事件；工具调用的参数是分片返回的，需累积后统一 json 解析。
        """
        url = self.config.base_url + "/chat/completions"
        headers = {
            "Authorization": "Bearer " + self.config.api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools

        resp = self.session.post(
            url, headers=headers, json=payload, stream=True, timeout=(10, 180)
        )
        if resp.status_code != 200:
            resp.close()
            raise LLMError(
                "DeepSeek API 返回错误 %d: %s" % (resp.status_code, resp.text[:500])
            )

        tool_acc = {}  # index -> {"id": str, "name": str, "args": [str]}
        try:
            for raw_line in resp.iter_lines():
                if not raw_line:
                    continue
                line = raw_line.decode("utf-8", errors="replace")
                if not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}

                content = delta.get("content")
                if content:
                    yield {"type": "delta", "content": content}

                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    acc = tool_acc.setdefault(idx, {"id": "", "name": "", "args": []})
                    if tc.get("id"):
                        acc["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        acc["name"] = fn["name"]
                    if fn.get("arguments"):
                        acc["args"].append(fn["arguments"])
        except requests.RequestException as e:
            raise LLMError("流式请求中断: %s" % e)
        finally:
            resp.close()

        if tool_acc:
            calls = []
            for idx in sorted(tool_acc):
                acc = tool_acc[idx]
                try:
                    args = json.loads("".join(acc["args"]) or "{}")
                except (json.JSONDecodeError, TypeError):
                    args = {}
                calls.append(ToolCall(id=acc["id"], name=acc["name"], arguments=args))
            yield {"type": "tool_calls", "tool_calls": calls}

    @staticmethod
    def _parse_response(data: dict) -> AssistantMessage:
        """把接口返回的 message 解析为 AssistantMessage。"""
        try:
            raw = data["choices"][0]["message"]
        except (KeyError, IndexError):
            raise LLMError("接口响应结构异常: %s" % json.dumps(data, ensure_ascii=False)[:300])

        tool_calls = []
        for tc in raw.get("tool_calls") or []:
            fn = tc.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except (json.JSONDecodeError, TypeError):
                args = {}
            tool_calls.append(
                ToolCall(id=tc.get("id", ""), name=fn.get("name", ""), arguments=args)
            )

        return AssistantMessage(content=raw.get("content"), tool_calls=tool_calls)

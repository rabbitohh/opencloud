from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from typing import Any


class DeepSeekClient:
    """Small OpenAI-compatible Chat Completions client for DeepSeek."""

    def __init__(
        self,
        api_key: str,
        model: str = "deepseek-v4-flash",
        temperature: float = 0.2,
        base_url: str = "https://api.deepseek.com",
        timeout: int = 60,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        request = urllib.request.Request(
            url=f"{self.base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"DeepSeek API 返回错误 {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"无法连接 DeepSeek API: {exc.reason}") from exc

        data = json.loads(body)
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"DeepSeek API 响应中没有 choices: {data}")
        return choices[0]["message"]

    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        on_delta: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        content_parts: list[str] = []
        tool_calls: dict[int, dict[str, Any]] = {}
        message: dict[str, Any] = {"role": "assistant", "content": ""}

        for chunk in self._stream_chunks(payload):
            choices = chunk.get("choices") or []
            if not choices:
                continue

            delta = choices[0].get("delta") or {}
            role = delta.get("role")
            if role:
                message["role"] = role

            content = delta.get("content")
            if content:
                content_parts.append(content)
                if on_delta is not None:
                    on_delta(content)

            for tool_call_delta in delta.get("tool_calls") or []:
                self._merge_tool_call_delta(tool_calls, tool_call_delta)

        message["content"] = "".join(content_parts)
        if tool_calls:
            message["tool_calls"] = [tool_calls[index] for index in sorted(tool_calls)]
        return message

    def _stream_chunks(self, payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
        request = urllib.request.Request(
            url=f"{self.base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line or line.startswith(":") or not line.startswith("data:"):
                        continue

                    data_line = line.removeprefix("data:").strip()
                    if data_line == "[DONE]":
                        break
                    yield json.loads(data_line)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"DeepSeek API 杩斿洖閿欒 {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"鏃犳硶杩炴帴 DeepSeek API: {exc.reason}") from exc

    @staticmethod
    def _merge_tool_call_delta(
        tool_calls: dict[int, dict[str, Any]],
        tool_call_delta: dict[str, Any],
    ) -> None:
        index = int(tool_call_delta.get("index", len(tool_calls)))
        tool_call = tool_calls.setdefault(
            index,
            {
                "id": "",
                "type": "function",
                "function": {"name": "", "arguments": ""},
            },
        )

        if tool_call_delta.get("id"):
            tool_call["id"] = tool_call_delta["id"]
        if tool_call_delta.get("type"):
            tool_call["type"] = tool_call_delta["type"]

        function_delta = tool_call_delta.get("function") or {}
        if function_delta.get("name"):
            tool_call["function"]["name"] += function_delta["name"]
        if function_delta.get("arguments") is not None:
            tool_call["function"]["arguments"] += function_delta["arguments"]

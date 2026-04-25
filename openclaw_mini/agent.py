from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from openclaw_mini.history import ChatHistory
from openclaw_mini.llm import DeepSeekClient
from openclaw_mini.tools.base import ToolRegistry


MAX_ROUNDS_STOP_MESSAGE = "达到最大推理轮数，已停止。你可以授权继续增加推理轮数，或缩小任务范围后继续提问。"


class MiniOpenClawAgent:
    def __init__(
        self,
        client: DeepSeekClient,
        tools: ToolRegistry,
        history: ChatHistory,
        max_rounds: int = 8,
    ) -> None:
        self.client = client
        self.tools = tools
        self.history = history
        self.max_rounds = max_rounds
        self.max_rounds_reached = False
        self.last_round_limit = max_rounds

    def run(self, user_input: str, *, max_rounds: int | None = None) -> str:
        return self._run(user_input, max_rounds=max_rounds)

    def run_stream(
        self,
        user_input: str,
        on_delta: Callable[[str], None] | None = None,
        on_event: Callable[[str], None] | None = None,
        max_rounds: int | None = None,
    ) -> str:
        printed_answer = False

        def print_delta(delta: str) -> None:
            nonlocal printed_answer
            if not printed_answer:
                print("\nAI: ", end="", flush=True)
                printed_answer = True
            print(delta, end="", flush=True)

        answer = self._run(
            user_input,
            on_delta=on_delta or print_delta,
            on_event=on_event,
            max_rounds=max_rounds,
        )
        if on_delta is None:
            if printed_answer:
                print()
            else:
                print(f"\nAI: {answer}")
        return answer

    def _run(
        self,
        user_input: str,
        on_delta: Callable[[str], None] | None = None,
        on_event: Callable[[str], None] | None = None,
        max_rounds: int | None = None,
    ) -> str:
        round_limit = max(1, max_rounds if max_rounds is not None else self.max_rounds)
        self.max_rounds_reached = False
        self.last_round_limit = round_limit
        self.history.add({"role": "user", "content": user_input})
        messages = list(self.history.messages)

        for round_index in range(1, round_limit + 1):
            self._emit_event(
                on_event,
                f"[Round {round_index}] Thought: 正在请求模型判断下一步...",
            )
            if on_delta is None:
                assistant_message = self.client.chat(messages, self.tools.to_openai_tools())
            else:
                assistant_message = self.client.chat_stream(
                    messages,
                    self.tools.to_openai_tools(),
                    on_delta=on_delta,
                )
            messages.append(assistant_message)
            self.history.add(assistant_message)

            tool_calls = assistant_message.get("tool_calls") or []
            if not tool_calls:
                content = assistant_message.get("content") or ""
                return content.strip() or "模型没有返回内容。"

            if on_delta is not None and assistant_message.get("content") and on_event is None:
                print()

            for tool_call in tool_calls:
                tool_name = tool_call.get("function", {}).get("name", "")
                raw_args = tool_call.get("function", {}).get("arguments") or "{}"
                self._emit_event(
                    on_event,
                    f"[Round {round_index}] Action: {tool_name}({raw_args})",
                )

                observation = self._execute_tool(tool_name, raw_args)
                self._emit_event(
                    on_event,
                    f"[Round {round_index}] Observation: {observation}",
                )

                tool_message = {
                    "role": "tool",
                    "tool_call_id": tool_call.get("id"),
                    "content": observation,
                }
                messages.append(tool_message)
                self.history.add(tool_message)

        self.max_rounds_reached = True
        final_message = MAX_ROUNDS_STOP_MESSAGE
        self.history.add({"role": "assistant", "content": final_message})
        return final_message

    @staticmethod
    def _emit_event(on_event: Callable[[str], None] | None, message: str) -> None:
        if on_event is None:
            print(f"\n{message}" if "Thought:" in message else message)
        else:
            on_event(message)

    def _execute_tool(self, tool_name: str, raw_args: str) -> str:
        try:
            args = json.loads(raw_args)
            if not isinstance(args, dict):
                return "工具参数必须是 JSON object。"
        except json.JSONDecodeError as exc:
            return f"工具参数 JSON 解析失败: {exc}"

        try:
            result = self.tools.call(tool_name, **args)
        except Exception as exc:
            result = {"ok": False, "error": str(exc)}

        return json.dumps(result, ensure_ascii=False)

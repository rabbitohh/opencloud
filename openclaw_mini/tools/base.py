from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Any]

    def to_openai_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            if name in self._tools:
                raise ValueError(f"工具已存在: {name}")
            self._tools[name] = Tool(name, description, parameters, func)
            return func

        return decorator

    def call(self, name: str, **kwargs: Any) -> Any:
        tool = self._tools.get(name)
        if tool is None:
            raise ValueError(f"未知工具: {name}")
        return tool.handler(**kwargs)

    def to_openai_tools(self) -> list[dict[str, Any]]:
        return [tool.to_openai_tool() for tool in self._tools.values()]

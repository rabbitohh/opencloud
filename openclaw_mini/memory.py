from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


MAX_MEMORY_CONTENT_CHARS = 1000
MAX_SYSTEM_MEMORY_CHARS = 6000


class MemoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "memories": []}

        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"version": 1, "memories": []}

        if isinstance(data, list):
            return {"version": 1, "memories": [item for item in data if isinstance(item, dict)]}
        if not isinstance(data, dict):
            return {"version": 1, "memories": []}

        memories = data.get("memories")
        if not isinstance(memories, list):
            memories = []
        return {"version": 1, "memories": [item for item in memories if isinstance(item, dict)]}

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def add(
        self,
        content: str,
        *,
        category: str = "general",
        source: str = "conversation",
        importance: int = 3,
    ) -> dict[str, Any]:
        clean_content = content.strip()
        if not clean_content:
            raise ValueError("记忆内容不能为空。")
        if len(clean_content) > MAX_MEMORY_CONTENT_CHARS:
            clean_content = clean_content[:MAX_MEMORY_CONTENT_CHARS].rstrip()

        clean_category = (category or "general").strip()[:40] or "general"
        clean_source = (source or "conversation").strip()[:80] or "conversation"
        try:
            clean_importance = min(max(int(importance), 1), 5)
        except (TypeError, ValueError):
            clean_importance = 3

        data = self._load()
        now = self._now()
        memory = {
            "id": uuid4().hex[:12],
            "content": clean_content,
            "category": clean_category,
            "source": clean_source,
            "importance": clean_importance,
            "created_at": now,
            "updated_at": now,
        }
        data["memories"].append(memory)
        self._save(data)
        return memory

    def list(self) -> list[dict[str, Any]]:
        return self._load()["memories"]

    def render_for_system_prompt(self) -> str:
        memories = sorted(
            self.list(),
            key=lambda item: (int(item.get("importance", 3)), str(item.get("updated_at", ""))),
            reverse=True,
        )
        if not memories:
            return ""

        lines = ["\n长期记忆："]
        for index, memory in enumerate(memories, start=1):
            content = str(memory.get("content", "")).strip()
            if not content:
                continue
            category = str(memory.get("category") or "general").strip()
            importance = memory.get("importance", 3)
            lines.append(f"{index}. [{category} | importance {importance}] {content}")

            rendered = "\n".join(lines)
            if len(rendered) >= MAX_SYSTEM_MEMORY_CHARS:
                return rendered[:MAX_SYSTEM_MEMORY_CHARS].rstrip() + "\n..."

        return "\n".join(lines)

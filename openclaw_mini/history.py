from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


SYSTEM_PROMPT = """你是 OpenClaw Mini，一个会使用本地工具完成任务的 Python 智能体。

工作方式：
1. 先理解用户目标，再判断是否需要调用工具。
2. 如果需要本地文件、目录或命令结果，使用可用工具获取事实。
3. 工具结果是 Observation，需要基于结果继续推理或给出最终答复。
4. 不要编造工具执行结果；不确定时继续调用工具确认。
5. 本地工具只能操作安全工作目录内的文件。
"""


class ChatHistory:
    def __init__(self, path: Path, session_id: str | None = None) -> None:
        self.path = path
        self._store = self._load_store()
        self.session_id = session_id or self._store.get("active_session_id")
        if not self.session_id or not self._find_session(self.session_id):
            self.session_id = self.create_session(save=False)
        self._store["active_session_id"] = self.session_id
        self.save()

    @property
    def messages(self) -> list[dict[str, Any]]:
        return self._current_session()["messages"]

    def _load_store(self) -> dict[str, Any]:
        if not self.path.exists():
            session = self._new_session("新对话")
            return {"version": 2, "active_session_id": session["id"], "sessions": [session]}

        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            session = self._new_session("新对话")
            return {"version": 2, "active_session_id": session["id"], "sessions": [session]}

        if isinstance(data, list):
            session = self._new_session("历史对话", messages=self._normalize_messages(data))
            return {"version": 2, "active_session_id": session["id"], "sessions": [session]}

        if not isinstance(data, dict):
            session = self._new_session("新对话")
            return {"version": 2, "active_session_id": session["id"], "sessions": [session]}

        raw_sessions = data.get("sessions")
        sessions = []
        if isinstance(raw_sessions, list):
            for raw_session in raw_sessions:
                session = self._normalize_session(raw_session)
                if session:
                    sessions.append(session)

        if not sessions:
            sessions.append(self._new_session("新对话"))

        active_session_id = data.get("active_session_id")
        if not isinstance(active_session_id, str) or not any(item["id"] == active_session_id for item in sessions):
            active_session_id = sessions[0]["id"]

        return {"version": 2, "active_session_id": active_session_id, "sessions": sessions}

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _new_session(
        self,
        title: str = "新对话",
        messages: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        now = self._now()
        return {
            "id": uuid4().hex,
            "title": title,
            "created_at": now,
            "updated_at": now,
            "messages": self._normalize_messages(messages or []),
        }

    @staticmethod
    def _normalize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        valid_messages = [message for message in messages if isinstance(message, dict)]
        if not valid_messages:
            return [{"role": "system", "content": SYSTEM_PROMPT}]

        if valid_messages[0].get("role") != "system":
            valid_messages.insert(0, {"role": "system", "content": SYSTEM_PROMPT})
        elif not valid_messages[0].get("content"):
            valid_messages[0]["content"] = SYSTEM_PROMPT
        return valid_messages

    def _normalize_session(self, raw_session: object) -> dict[str, Any] | None:
        if not isinstance(raw_session, dict):
            return None

        session_id = raw_session.get("id")
        if not isinstance(session_id, str) or not session_id:
            session_id = uuid4().hex

        title = raw_session.get("title")
        if not isinstance(title, str) or not title.strip():
            title = "新对话"

        created_at = raw_session.get("created_at")
        updated_at = raw_session.get("updated_at")
        if not isinstance(created_at, str):
            created_at = self._now()
        if not isinstance(updated_at, str):
            updated_at = created_at

        raw_messages = raw_session.get("messages")
        messages = self._normalize_messages(raw_messages if isinstance(raw_messages, list) else [])

        return {
            "id": session_id,
            "title": title.strip(),
            "created_at": created_at,
            "updated_at": updated_at,
            "messages": messages,
        }

    def _find_session(self, session_id: str) -> dict[str, Any] | None:
        for session in self._store["sessions"]:
            if session["id"] == session_id:
                return session
        return None

    def _current_session(self) -> dict[str, Any]:
        session = self._find_session(self.session_id)
        if session is None:
            self.session_id = self.create_session(save=False)
            session = self._find_session(self.session_id)
            if session is None:
                raise RuntimeError("无法创建会话。")
        return session

    def list_sessions(self) -> list[dict[str, str]]:
        sessions = sorted(
            self._store["sessions"],
            key=lambda item: item.get("updated_at", ""),
            reverse=True,
        )
        return [
            {
                "id": session["id"],
                "title": session.get("title") or "新对话",
                "updated_at": session.get("updated_at") or "",
            }
            for session in sessions
        ]

    def create_session(self, save: bool = True) -> str:
        session = self._new_session("新对话")
        self._store["sessions"].append(session)
        self.session_id = session["id"]
        self._store["active_session_id"] = self.session_id
        if save:
            self.save()
        return self.session_id

    def switch_session(self, session_id: str) -> None:
        if not self._find_session(session_id):
            raise ValueError("会话不存在。")
        self.session_id = session_id
        self._store["active_session_id"] = session_id
        self.save()

    def current_title(self) -> str:
        return self._current_session().get("title") or "新对话"

    def add(self, message: dict[str, Any]) -> None:
        session = self._current_session()
        session["messages"].append(message)
        session["updated_at"] = self._now()
        if message.get("role") == "user" and session.get("title") == "新对话":
            content = str(message.get("content", "")).strip()
            if content:
                session["title"] = content.splitlines()[0][:24]
        self.save()

    def extend(self, messages: list[dict[str, Any]]) -> None:
        session = self._current_session()
        session["messages"].extend(messages)
        session["updated_at"] = self._now()
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._store, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


@dataclass(frozen=True)
class Config:
    deepseek_api_key: str
    model: str
    workspace: Path
    history_path: Path
    memory_path: Path
    max_rounds: int
    temperature: float
    baidu_speech_api_key: str
    baidu_speech_secret_key: str
    baidu_speech_cuid: str
    baidu_speech_dev_pid: int

    @classmethod
    def from_env(cls) -> "Config":
        root = Path.cwd()
        load_dotenv(root / ".env")

        api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("请先设置环境变量 DEEPSEEK_API_KEY。")

        workspace = Path(os.getenv("OPENCLAW_WORKSPACE", root / "workspace")).resolve()
        history_path = Path(os.getenv("OPENCLAW_HISTORY", root / "chat_history.json")).resolve()
        memory_path = Path(os.getenv("OPENCLAW_MEMORY", root / "memory.json")).resolve()

        return cls(
            deepseek_api_key=api_key,
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip() or "deepseek-chat",
            workspace=workspace,
            history_path=history_path,
            memory_path=memory_path,
            max_rounds=int(os.getenv("OPENCLAW_MAX_ROUNDS", "8")),
            temperature=float(os.getenv("OPENCLAW_TEMPERATURE", "0.2")),
            baidu_speech_api_key=os.getenv("BAIDU_SPEECH_API_KEY", "").strip(),
            baidu_speech_secret_key=os.getenv("BAIDU_SPEECH_SECRET_KEY", "").strip(),
            baidu_speech_cuid=os.getenv("BAIDU_SPEECH_CUID", f"opencloud-{uuid.getnode():012x}").strip(),
            baidu_speech_dev_pid=int(os.getenv("BAIDU_SPEECH_DEV_PID", "1537")),
        )

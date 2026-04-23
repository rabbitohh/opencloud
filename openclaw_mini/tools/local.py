from __future__ import annotations

import os
import platform
import subprocess
import webbrowser
from pathlib import Path
from typing import Any

from openclaw_mini.tools.base import ToolRegistry


def build_local_tool_registry(workspace: Path) -> ToolRegistry:
    registry = ToolRegistry()
    root = workspace.resolve()
    root.mkdir(parents=True, exist_ok=True)

    def safe_path(path: str = ".") -> Path:
        candidate = (root / path).resolve()
        if candidate != root and root not in candidate.parents:
            raise ValueError(f"路径越界，禁止访问安全目录外的文件: {path}")
        return candidate

    @registry.register(
        name="list_files",
        description="列出安全工作目录内的文件和子目录。",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对 workspace 的目录路径，默认 ."},
                "recursive": {"type": "boolean", "description": "是否递归列出，默认 false"},
            },
            "required": [],
        },
    )
    def list_files(path: str = ".", recursive: bool = False) -> dict[str, Any]:
        target = safe_path(path)
        if not target.exists():
            return {"ok": False, "error": f"路径不存在: {path}"}
        if not target.is_dir():
            return {"ok": False, "error": f"不是目录: {path}"}

        iterator = target.rglob("*") if recursive else target.iterdir()
        items = []
        for item in sorted(iterator):
            rel = item.relative_to(root).as_posix()
            items.append(
                {
                    "path": rel,
                    "type": "directory" if item.is_dir() else "file",
                    "size": item.stat().st_size if item.is_file() else None,
                }
            )
        return {"ok": True, "items": items}

    @registry.register(
        name="read_file",
        description="读取安全工作目录内的文本文件内容。",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对 workspace 的文件路径"},
                "max_chars": {
                    "type": "integer",
                    "description": "最多读取的字符数，默认 8000",
                },
            },
            "required": ["path"],
        },
    )
    def read_file(path: str, max_chars: int = 8000) -> dict[str, Any]:
        target = safe_path(path)
        if not target.exists():
            return {"ok": False, "error": f"文件不存在: {path}"}
        if not target.is_file():
            return {"ok": False, "error": f"不是文件: {path}"}

        max_chars = min(max(int(max_chars), 1), 100_000)
        content = target.read_text(encoding="utf-8", errors="replace")
        truncated = len(content) > max_chars
        return {
            "ok": True,
            "path": target.relative_to(root).as_posix(),
            "content": content[:max_chars],
            "truncated": truncated,
        }

    @registry.register(
        name="write_file",
        description="向安全工作目录内写入文本文件，可选择覆盖或追加。",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对 workspace 的文件路径"},
                "content": {"type": "string", "description": "要写入的文本内容"},
                "append": {"type": "boolean", "description": "是否追加写入，默认 false"},
            },
            "required": ["path", "content"],
        },
    )
    def write_file(path: str, content: str, append: bool = False) -> dict[str, Any]:
        if len(content) > 200_000:
            return {"ok": False, "error": "单次写入内容过大，限制为 200000 字符。"}

        target = safe_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        with target.open(mode, encoding="utf-8") as file:
            file.write(content)
        return {
            "ok": True,
            "path": target.relative_to(root).as_posix(),
            "bytes": target.stat().st_size,
            "append": append,
        }

    @registry.register(
        name="run_shell_command",
        description="在安全工作目录中执行少量白名单命令，用于查看环境或运行简单 Python 检查。",
        parameters={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "允许的命令，如 dir、ls、pwd、type file、cat file、python --version",
                },
                "timeout": {"type": "integer", "description": "超时时间秒数，默认 10，最大 30"},
            },
            "required": ["command"],
        },
    )
    def run_shell_command(command: str, timeout: int = 10) -> dict[str, Any]:
        args = command.strip().split()
        if not args:
            return {"ok": False, "error": "命令不能为空。"}

        executable = args[0].lower()
        allowed_simple = {"dir", "ls", "pwd"}
        allowed_file_view = {"type", "cat"}
        timeout = min(max(int(timeout), 1), 30)

        if executable in allowed_simple and len(args) == 1:
            if executable == "pwd":
                return {"ok": True, "stdout": str(root), "stderr": ""}

            items = []
            for item in sorted(root.iterdir()):
                kind = "<DIR>" if item.is_dir() else "     "
                size = "" if item.is_dir() else str(item.stat().st_size)
                items.append(f"{kind} {size:>10} {item.name}")
            return {"ok": True, "stdout": "\n".join(items), "stderr": ""}
        elif executable in allowed_file_view and len(args) == 2:
            target = safe_path(args[1])
            if not target.exists() or not target.is_file():
                return {"ok": False, "error": f"文件不存在或不是文件: {args[1]}"}
            content = target.read_text(encoding="utf-8", errors="replace")
            return {"ok": True, "stdout": content[-8000:], "stderr": ""}
        elif executable == "python" and args == ["python", "--version"]:
            safe_args = args
        else:
            return {
                "ok": False,
                "error": "命令不在白名单内。允许: dir, ls, pwd, type <file>, cat <file>, python --version",
            }

        completed = subprocess.run(
            safe_args,
            cwd=root,
            text=True,
            capture_output=True,
            timeout=timeout,
            shell=False,
        )
        return {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": completed.stdout[-8000:],
            "stderr": completed.stderr[-4000:],
        }

    @registry.register(
        name="open_file",
        description="Open a file or directory inside the safe workspace with the system default application.",
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path relative to workspace. Can be a file or directory.",
                },
            },
            "required": ["path"],
        },
    )
    def open_file(path: str) -> dict[str, Any]:
        target = safe_path(path)
        if not target.exists():
            return {"ok": False, "error": f"Path does not exist: {path}"}

        try:
            if platform.system() == "Windows":
                os.startfile(target)  # type: ignore[attr-defined]
            else:
                webbrowser.open(target.resolve().as_uri())
        except OSError as exc:
            return {"ok": False, "error": f"Failed to open path: {exc}"}

        rel_path = target.relative_to(root).as_posix()
        return {
            "ok": True,
            "path": rel_path or ".",
            "type": "directory" if target.is_dir() else "file",
        }

    return registry

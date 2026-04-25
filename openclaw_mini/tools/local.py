from __future__ import annotations

import json
import os
import platform
import re
import shlex
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timedelta, timezone, tzinfo
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from openclaw_mini.tools.base import ToolRegistry


HTTP_TIMEOUT_SECONDS = 12
MAX_WEB_BYTES = 2_000_000
USER_AGENT = "Opencloud/1.0 (+https://github.com/)"
SHELL_CONTROL_TOKENS = {"|", "||", "&", "&&", ";", "<", ">", ">>", "2>", "2>>"}

FIXED_TIMEZONE_ALIASES = {
    "utc": timezone.utc,
    "gmt": timezone.utc,
    "z": timezone.utc,
    "中国": timezone(timedelta(hours=8), "UTC+08:00"),
    "北京": timezone(timedelta(hours=8), "UTC+08:00"),
    "北京时间": timezone(timedelta(hours=8), "UTC+08:00"),
    "上海": timezone(timedelta(hours=8), "UTC+08:00"),
    "china": timezone(timedelta(hours=8), "UTC+08:00"),
    "beijing": timezone(timedelta(hours=8), "UTC+08:00"),
    "shanghai": timezone(timedelta(hours=8), "UTC+08:00"),
    "asia/shanghai": timezone(timedelta(hours=8), "UTC+08:00"),
    "hong kong": timezone(timedelta(hours=8), "UTC+08:00"),
    "asia/hong_kong": timezone(timedelta(hours=8), "UTC+08:00"),
    "tokyo": timezone(timedelta(hours=9), "UTC+09:00"),
    "asia/tokyo": timezone(timedelta(hours=9), "UTC+09:00"),
    "seoul": timezone(timedelta(hours=9), "UTC+09:00"),
    "asia/seoul": timezone(timedelta(hours=9), "UTC+09:00"),
}

IANA_TIMEZONE_ALIASES = {
    "london": "Europe/London",
    "paris": "Europe/Paris",
    "new york": "America/New_York",
    "los angeles": "America/Los_Angeles",
    "san francisco": "America/Los_Angeles",
    "berlin": "Europe/Berlin",
    "sydney": "Australia/Sydney",
}


def clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        return min(max(int(value), minimum), maximum)
    except (TypeError, ValueError):
        return default


def strip_matching_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def split_command(command: str) -> list[str]:
    return [strip_matching_quotes(arg) for arg in shlex.split(command.strip(), posix=False)]


def tail_text(value: Any, max_chars: int) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")[-max_chars:]
    return str(value)[-max_chars:]


def parse_timezone(value: str = "") -> tzinfo:
    clean_value = value.strip()
    if not clean_value:
        local_tz = datetime.now().astimezone().tzinfo
        if local_tz is None:
            return timezone.utc
        return local_tz

    lower_value = clean_value.lower()
    if lower_value in FIXED_TIMEZONE_ALIASES:
        return FIXED_TIMEZONE_ALIASES[lower_value]

    iana_key = IANA_TIMEZONE_ALIASES.get(lower_value, clean_value)
    try:
        return ZoneInfo(iana_key)
    except ZoneInfoNotFoundError:
        pass

    offset = lower_value
    for prefix in ("utc", "gmt"):
        if offset.startswith(prefix):
            offset = offset[len(prefix) :].strip()
            break
    if offset.startswith(("+", "-")):
        sign = 1 if offset[0] == "+" else -1
        offset_body = offset[1:]
        try:
            if ":" in offset_body:
                hours_text, minutes_text = offset_body.split(":", 1)
                hours = int(hours_text)
                minutes = int(minutes_text)
            else:
                hours = int(offset_body)
                minutes = 0
        except ValueError as exc:
            raise ValueError(
                "无法解析时区，请使用 UTC、UTC+8、+08:00 或 Asia/Shanghai 这类常见写法。"
            ) from exc

        if hours > 14 or minutes > 59:
            raise ValueError("UTC 偏移超出合理范围。")
        total_offset = timedelta(hours=hours, minutes=minutes) * sign
        return timezone(total_offset, f"UTC{offset[0]}{hours:02d}:{minutes:02d}")

    raise ValueError(
        "无法识别时区，请使用 UTC、UTC+8、+08:00、Asia/Shanghai，或安装 tzdata 后使用 IANA 时区。"
    )


def weather_text(items: list[dict[str, Any]], fallback: str = "") -> str:
    if not items:
        return fallback
    value = items[0].get("value")
    return str(value) if value is not None else fallback


def weather_description(data: dict[str, Any], language: str = "zh") -> str:
    default_description = weather_text(data.get("weatherDesc") or [])
    if language == "zh":
        return weather_text(data.get("lang_zh") or [], default_description).strip()
    return default_description.strip()


def weather_hour(value: Any) -> str:
    try:
        hour_value = int(value)
    except (TypeError, ValueError):
        return str(value or "")
    return f"{hour_value // 100:02d}:00"


def forecast_days_count(value: Any) -> int:
    return clamp_int(value, default=3, minimum=1, maximum=3)


def clean_url(value: str) -> str:
    url = value.strip()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("只支持 http 或 https URL。")
    return url


def make_web_request(url: str) -> urllib.request.Request:
    return urllib.request.Request(
        url=url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8",
        },
        method="GET",
    )


def decode_web_body(body: bytes, content_type: str) -> str:
    charset = ""
    match = re.search(r"charset=([\w.-]+)", content_type, re.IGNORECASE)
    if match:
        charset = match.group(1)

    for encoding in (charset, "utf-8", "gb18030", "latin-1"):
        if not encoding:
            continue
        try:
            return body.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue

    return body.decode("utf-8", errors="replace")


def compact_text(value: str) -> str:
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class WebTextParser(HTMLParser):
    block_tags = {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "dd",
        "div",
        "dl",
        "dt",
        "figcaption",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "td",
        "th",
        "tr",
        "ul",
    }
    ignored_tags = {"script", "style", "noscript", "svg", "canvas"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self._ignore_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.ignored_tags:
            self._ignore_depth += 1
            return
        if tag == "title":
            self._in_title = True
        if tag in self.block_tags:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.ignored_tags and self._ignore_depth:
            self._ignore_depth -= 1
            return
        if tag == "title":
            self._in_title = False
        if tag in self.block_tags:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._ignore_depth:
            return
        if self._in_title:
            self.title_parts.append(data)
            return
        self.parts.append(data)

    @property
    def title(self) -> str:
        return compact_text(" ".join(self.title_parts))

    @property
    def text(self) -> str:
        return compact_text(" ".join(self.parts))


class DuckDuckGoSearchParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._link_href: str | None = None
        self._link_parts: list[str] = []
        self._snippet_index: int | None = None
        self._snippet_parts: list[str] = []

    @staticmethod
    def _attrs_dict(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        return {key: value or "" for key, value in attrs}

    @staticmethod
    def _class_names(attrs: dict[str, str]) -> set[str]:
        return set(attrs.get("class", "").split())

    @staticmethod
    def _normalize_result_url(href: str) -> str:
        if href.startswith("//"):
            href = "https:" + href
        parsed = urllib.parse.urlparse(href)
        query = urllib.parse.parse_qs(parsed.query)
        if "uddg" in query and query["uddg"]:
            return query["uddg"][0]
        return href

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = self._attrs_dict(attrs)
        class_names = self._class_names(attr_map)

        if tag == "a" and "result__a" in class_names:
            self._link_href = attr_map.get("href", "")
            self._link_parts = []
            return

        if "result__snippet" in class_names and self.results:
            self._snippet_index = len(self.results) - 1
            self._snippet_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._link_href is not None:
            title = compact_text(" ".join(self._link_parts))
            if title:
                self.results.append(
                    {
                        "title": title,
                        "url": self._normalize_result_url(self._link_href),
                        "snippet": "",
                    }
                )
            self._link_href = None
            self._link_parts = []
            return

        if self._snippet_index is not None and tag in {"a", "div"}:
            snippet = compact_text(" ".join(self._snippet_parts))
            if snippet and self._snippet_index < len(self.results):
                self.results[self._snippet_index]["snippet"] = snippet
            self._snippet_index = None
            self._snippet_parts = []

    def handle_data(self, data: str) -> None:
        if self._link_href is not None:
            self._link_parts.append(data)
        if self._snippet_index is not None:
            self._snippet_parts.append(data)


def fetch_web_resource(url: str) -> dict[str, Any]:
    request = make_web_request(url)
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            body = response.read(MAX_WEB_BYTES + 1)
            final_url = response.geturl()
            status = getattr(response, "status", 200)
            content_type = response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        return {"ok": False, "error": f"网页返回 HTTP {exc.code}。", "url": url}
    except urllib.error.URLError as exc:
        return {"ok": False, "error": f"无法连接网页: {exc.reason}", "url": url}
    except TimeoutError:
        return {"ok": False, "error": "连接网页超时。", "url": url}

    too_large = len(body) > MAX_WEB_BYTES
    body = body[:MAX_WEB_BYTES]
    raw_text = decode_web_body(body, content_type)
    lower_content_type = content_type.lower()

    if "html" in lower_content_type or raw_text.lstrip().lower().startswith(("<!doctype html", "<html")):
        parser = WebTextParser()
        parser.feed(raw_text)
        title = parser.title
        text = parser.text
    elif lower_content_type.startswith("text/") or "json" in lower_content_type or "xml" in lower_content_type:
        title = ""
        text = compact_text(raw_text)
    else:
        return {
            "ok": False,
            "error": f"暂不支持读取该内容类型: {content_type or 'unknown'}",
            "url": url,
            "final_url": final_url,
            "status": status,
        }

    return {
        "ok": True,
        "url": url,
        "final_url": final_url,
        "status": status,
        "content_type": content_type,
        "title": title,
        "text": text,
        "raw_text": raw_text,
        "download_truncated": too_large,
    }


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
        description="在安全工作目录中执行少量白名单命令，用于查看环境、读取文件或运行 workspace 内的 Python 脚本。",
        parameters={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "允许的命令，如 dir、ls、pwd、type file、cat file、python --version、python script.py [args...]",
                },
                "timeout": {"type": "integer", "description": "超时时间秒数，默认 10，最大 30"},
            },
            "required": ["command"],
        },
    )
    def run_shell_command(command: str, timeout: int = 10) -> dict[str, Any]:
        try:
            args = split_command(command)
        except ValueError as exc:
            return {"ok": False, "error": f"命令解析失败: {exc}"}

        if not args:
            return {"ok": False, "error": "命令不能为空。"}
        if any(arg in SHELL_CONTROL_TOKENS for arg in args):
            return {"ok": False, "error": "不支持 shell 控制符、管道或重定向。请改用脚本内文件读写。"}

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
            try:
                target = safe_path(args[1])
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            if not target.exists() or not target.is_file():
                return {"ok": False, "error": f"文件不存在或不是文件: {args[1]}"}
            content = target.read_text(encoding="utf-8", errors="replace")
            return {"ok": True, "stdout": content[-8000:], "stderr": ""}
        elif executable in {"python", "python3"}:
            if args == [args[0], "--version"]:
                safe_args = [sys.executable, "--version"]
            elif len(args) >= 2 and not args[1].startswith("-"):
                try:
                    script = safe_path(args[1])
                except ValueError as exc:
                    return {"ok": False, "error": str(exc)}
                if not script.exists() or not script.is_file():
                    return {"ok": False, "error": f"Python 脚本不存在或不是文件: {args[1]}"}
                if script.suffix.lower() != ".py":
                    return {"ok": False, "error": f"只允许运行 .py 脚本: {args[1]}"}
                safe_args = [sys.executable, str(script), *args[2:]]
            else:
                return {
                    "ok": False,
                    "error": "Python 命令只允许: python --version 或 python <workspace内.py脚本> [参数...]",
                }
        else:
            return {
                "ok": False,
                "error": "命令不在白名单内。允许: dir, ls, pwd, type <file>, cat <file>, python --version, python <script.py> [args...]",
            }

        try:
            completed = subprocess.run(
                safe_args,
                cwd=root,
                text=True,
                capture_output=True,
                timeout=timeout,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            return {
                "ok": False,
                "error": f"命令执行超时（{timeout} 秒）。",
                "stdout": tail_text(exc.stdout, 8000),
                "stderr": tail_text(exc.stderr, 4000),
            }
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

    @registry.register(
        name="web_search",
        description=(
            "Search the web and return result titles, snippets, and URLs. Use this "
            "for external knowledge, current information, or topics you are not sure about."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keywords or question."},
                "max_results": {
                    "type": "integer",
                    "description": "Number of search results to return, default 5, range 1-10.",
                    "minimum": 1,
                    "maximum": 10,
                },
            },
            "required": ["query"],
        },
    )
    def web_search(query: str, max_results: int = 5) -> dict[str, Any]:
        clean_query = query.strip()
        if not clean_query:
            return {"ok": False, "error": "query 不能为空。"}

        result_count = clamp_int(max_results, default=5, minimum=1, maximum=10)
        search_url = "https://duckduckgo.com/html/?" + urllib.parse.urlencode({"q": clean_query})
        fetched = fetch_web_resource(search_url)
        if not fetched.get("ok"):
            fetched["source"] = "duckduckgo.com/html"
            fetched["query"] = clean_query
            return fetched

        raw_text = str(fetched.get("raw_text", ""))
        if False and is_google_shell_page(raw_text):
            return {
                "ok": False,
                "error": (
                    "Google 返回了需要 JavaScript 或额外验证的壳页，当前环境无法直接解析搜索结果。"
                    "如需稳定使用 Google，请在 .env 中配置 GOOGLE_SEARCH_API_KEY 和 GOOGLE_SEARCH_CX，"
                    "改走官方 Google Custom Search API。"
                ),
                "source": "duckduckgo.com/html",
                "query": clean_query,
                "search_url": search_url,
            }

        parser = DuckDuckGoSearchParser()
        parser.feed(str(fetched.get("raw_text", "")))
        results = parser.results[:result_count]
        if not results:
            return {
                "ok": False,
                "error": "没有解析到搜索结果，可能被搜索服务限制或页面结构已变化。",
                "source": "duckduckgo.com/html",
                "query": clean_query,
                "search_url": search_url,
            }

        return {
            "ok": True,
            "source": "duckduckgo.com/html",
            "query": clean_query,
            "search_url": search_url,
            "results": results,
        }

    @registry.register(
        name="fetch_url",
        description=(
            "Fetch a URL and return readable text extracted from HTML or text content. "
            "Use this after web_search to read a source page before answering."
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "HTTP or HTTPS URL to read."},
                "max_chars": {
                    "type": "integer",
                    "description": "Maximum characters of readable text to return, default 12000, range 1000-50000.",
                    "minimum": 1000,
                    "maximum": 50000,
                },
            },
            "required": ["url"],
        },
    )
    def fetch_url(url: str, max_chars: int = 12000) -> dict[str, Any]:
        try:
            target_url = clean_url(url)
        except ValueError as exc:
            return {"ok": False, "error": str(exc), "url": url}

        fetched = fetch_web_resource(target_url)
        if not fetched.get("ok"):
            return fetched

        limit = clamp_int(max_chars, default=12000, minimum=1000, maximum=50000)
        text = str(fetched.get("text", ""))
        truncated = len(text) > limit
        fetched["text"] = text[:limit]
        fetched["truncated"] = truncated
        fetched.pop("raw_text", None)
        return fetched

    @registry.register(
        name="get_current_time",
        description="查看当前日期和时间。可按本机时区、UTC 偏移或常见城市/时区别名返回时间。",
        parameters={
            "type": "object",
            "properties": {
                "timezone": {
                    "type": "string",
                    "description": "可选。留空使用本机时区；也可传 UTC、UTC+8、+08:00、Asia/Shanghai、Beijing、Tokyo 等。",
                },
            },
            "required": [],
        },
    )
    def get_current_time(timezone: str = "") -> dict[str, Any]:
        try:
            tz = parse_timezone(timezone)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        now = datetime.now(tz)
        return {
            "ok": True,
            "timezone": now.tzname(),
            "utc_offset": now.strftime("%z"),
            "iso": now.isoformat(timespec="seconds"),
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M:%S"),
            "weekday": now.strftime("%A"),
        }

    @registry.register(
        name="get_weather_forecast",
        description="查看指定城市或地区的天气预报。需要联网，可返回当前天气和未来 1-3 天预报。",
        parameters={
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "城市、地区或地址，例如 北京、上海、Shenzhen、New York。",
                },
                "language": {
                    "type": "string",
                    "description": "天气描述语言，默认 zh，可选 zh 或 en。",
                    "enum": ["zh", "en"],
                },
                "days": {
                    "type": "integer",
                    "description": "返回预报天数，默认 3，范围 1-3。",
                    "minimum": 1,
                    "maximum": 3,
                },
            },
            "required": ["location"],
        },
    )
    def get_weather_forecast(
        location: str,
        language: str = "zh",
        days: int = 3,
    ) -> dict[str, Any]:
        clean_location = location.strip()
        if not clean_location:
            return {"ok": False, "error": "location 不能为空。"}

        language = "zh" if language not in {"zh", "en"} else language
        days = forecast_days_count(days)
        query_location = urllib.parse.quote(clean_location, safe="")
        url = f"https://wttr.in/{query_location}?format=j1&lang={language}"
        request = urllib.request.Request(
            url=url,
            headers={"User-Agent": "Opencloud/1.0"},
            method="GET",
        )

        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return {"ok": False, "error": f"天气服务返回错误 {exc.code}。"}
        except urllib.error.URLError as exc:
            return {"ok": False, "error": f"无法连接天气服务: {exc.reason}"}
        except TimeoutError:
            return {"ok": False, "error": "连接天气服务超时。"}

        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            return {"ok": False, "error": f"天气服务响应解析失败: {exc}"}

        conditions = data.get("current_condition") or []
        if not conditions:
            return {"ok": False, "error": f"没有查到 {clean_location} 的天气。"}

        current = conditions[0]
        nearest_area = (data.get("nearest_area") or [{}])[0]
        area_name = weather_text(nearest_area.get("areaName") or [], clean_location)
        region = weather_text(nearest_area.get("region") or [])
        country = weather_text(nearest_area.get("country") or [])
        forecast_days = []
        for day in (data.get("weather") or [])[:days]:
            hourly = []
            for item in day.get("hourly") or []:
                hourly.append(
                    {
                        "time": weather_hour(item.get("time")),
                        "description": weather_description(item, language),
                        "temperature_c": item.get("tempC"),
                        "feels_like_c": item.get("FeelsLikeC"),
                        "chance_of_rain_percent": item.get("chanceofrain"),
                        "chance_of_snow_percent": item.get("chanceofsnow"),
                        "precipitation_mm": item.get("precipMM"),
                        "humidity_percent": item.get("humidity"),
                        "wind_speed_kmph": item.get("windspeedKmph"),
                        "wind_direction": item.get("winddir16Point"),
                    }
                )

            forecast_days.append(
                {
                    "date": day.get("date"),
                    "astronomy": (day.get("astronomy") or [{}])[0],
                    "summary": {
                        "max_temperature_c": day.get("maxtempC"),
                        "min_temperature_c": day.get("mintempC"),
                        "avg_temperature_c": day.get("avgtempC"),
                        "total_snow_cm": day.get("totalSnow_cm"),
                        "sun_hour": day.get("sunHour"),
                        "uv_index": day.get("uvIndex"),
                    },
                    "hourly": hourly,
                }
            )

        return {
            "ok": True,
            "source": "wttr.in",
            "query": clean_location,
            "days": days,
            "location": {
                "name": area_name,
                "region": region,
                "country": country,
                "latitude": nearest_area.get("latitude"),
                "longitude": nearest_area.get("longitude"),
            },
            "observed_at": current.get("localObsDateTime") or current.get("observation_time"),
            "current": {
                "description": weather_description(current, language),
                "temperature_c": current.get("temp_C"),
                "feels_like_c": current.get("FeelsLikeC"),
                "humidity_percent": current.get("humidity"),
                "wind_speed_kmph": current.get("windspeedKmph"),
                "wind_direction": current.get("winddir16Point"),
                "pressure_hpa": current.get("pressure"),
                "precipitation_mm": current.get("precipMM"),
                "visibility_km": current.get("visibility"),
                "uv_index": current.get("uvIndex"),
            },
            "forecast": forecast_days,
        }

    return registry

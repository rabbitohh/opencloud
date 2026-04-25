from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request


class BaiduSpeechRecognizer:
    """Small REST client for Baidu short speech recognition."""

    TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
    ASR_URL = "http://vop.baidu.com/server_api"

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        cuid: str,
        dev_pid: int = 1537,
        timeout: int = 30,
    ) -> None:
        self.api_key = api_key.strip()
        self.secret_key = secret_key.strip()
        self.cuid = cuid.strip()[:60] or "opencloud"
        self.dev_pid = dev_pid
        self.timeout = timeout
        self._access_token: str | None = None
        self._token_expires_at = 0.0

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.secret_key)

    def recognize_pcm(self, audio: bytes, *, rate: int = 16000) -> str:
        if not self.is_configured:
            raise RuntimeError("请先在 .env 中填写 BAIDU_SPEECH_API_KEY 和 BAIDU_SPEECH_SECRET_KEY。")
        if not audio:
            raise RuntimeError("没有录到可识别的音频。")

        payload = {
            "format": "pcm",
            "rate": rate,
            "channel": 1,
            "cuid": self.cuid,
            "token": self._get_access_token(),
            "dev_pid": self.dev_pid,
            "len": len(audio),
            "speech": base64.b64encode(audio).decode("ascii"),
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.ASR_URL,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        response = self._open_json(request, "百度语音识别请求失败")
        err_no = int(response.get("err_no", -1))
        if err_no != 0:
            err_msg = response.get("err_msg", "unknown error")
            raise RuntimeError(f"百度语音识别失败 {err_no}: {err_msg}")

        result = response.get("result") or []
        if not result:
            raise RuntimeError("百度语音识别成功返回，但没有识别文本。")
        return str(result[0]).strip()

    def _get_access_token(self) -> str:
        if self._access_token and time.time() < self._token_expires_at:
            return self._access_token

        query = urllib.parse.urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": self.api_key,
                "client_secret": self.secret_key,
            }
        )
        request = urllib.request.Request(
            f"{self.TOKEN_URL}?{query}",
            data=b"",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )

        response = self._open_json(request, "获取百度 access_token 失败")
        token = response.get("access_token")
        if not token:
            error = response.get("error") or "missing access_token"
            description = response.get("error_description") or response
            raise RuntimeError(f"获取百度 access_token 失败: {error} {description}")

        expires_in = int(response.get("expires_in", 2592000))
        self._access_token = str(token)
        self._token_expires_at = time.time() + max(60, expires_in - 300)
        return self._access_token

    def _open_json(self, request: urllib.request.Request, label: str) -> dict[str, object]:
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{label}，HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"{label}: {exc.reason}") from exc

        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{label}，响应不是 JSON: {body[:300]}") from exc
        if not isinstance(data, dict):
            raise RuntimeError(f"{label}，响应格式异常: {data}")
        return data

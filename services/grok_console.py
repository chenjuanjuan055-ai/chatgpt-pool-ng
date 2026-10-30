"""Grok 控制台（独立模块）：对接 grok2api 的管理 API，做本项目的 Grok 面板。

与 GPT 号池完全隔离：自己的配置文件、自己的连接、不参与号池调度。
"""
from __future__ import annotations

import json
import threading
import uuid
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

DATA_FILE = Path(__file__).resolve().parents[1] / "data" / "grok_console.json"

_EVENTS: list[dict[str, Any]] = []
_EVENT_LOCK = threading.Lock()


def record_event(kind: str, message: str, detail: str = "") -> None:
    with _EVENT_LOCK:
        _EVENTS.append({"ts": time.time(), "kind": kind, "message": message, "detail": detail[:300]})
        del _EVENTS[:-200]

_DEFAULT: dict[str, Any] = {
    "base_url": "",
    "username": "",
    "password": "",
    "api_key": "",
}


class GrokConsoleError(RuntimeError):
    pass


class GrokConsole:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._token = ""
        self._token_at = 0.0

    # ---------- settings ----------
    def settings(self) -> dict[str, Any]:
        if not DATA_FILE.exists():
            return dict(_DEFAULT)
        try:
            data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            return {**_DEFAULT, **{k: str(v) for k, v in data.items() if k in _DEFAULT}}
        except Exception:
            return dict(_DEFAULT)

    def save_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        current = self.settings()
        for key in _DEFAULT:
            if key in payload and payload[key] is not None:
                current[key] = str(payload[key]).strip()
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        DATA_FILE.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        with self._lock:
            self._token = ""
        return self.public_settings()

    def public_settings(self) -> dict[str, Any]:
        data = self.settings()
        key = data.get("api_key") or ""
        return {
            "base_url": data.get("base_url") or "",
            "username": data.get("username") or "",
            "api_key_masked": f"{key[:8]}***{key[-4:]}" if len(key) > 14 else ("***" if key else ""),
            "configured": bool(data.get("base_url") and data.get("username") and data.get("password")),
        }

    # ---------- http ----------
    def _request(self, path: str, method: str = "GET", payload: dict[str, Any] | None = None,
                 token: str | None = None, base: str | None = None, timeout: int = 25) -> Any:
        settings = self.settings()
        base = (base or settings.get("base_url") or "").rstrip("/")
        if not base:
            raise GrokConsoleError("未配置 grok2api 地址")
        headers = {"content-type": "application/json"}
        if token:
            headers["authorization"] = f"Bearer {token}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8", "replace")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise GrokConsoleError(f"HTTP {exc.code}: {detail}") from exc
        except Exception as exc:
            raise GrokConsoleError(f"{type(exc).__name__}: {exc}") from exc

    def _login(self) -> str:
        settings = self.settings()
        if not settings.get("username") or not settings.get("password"):
            raise GrokConsoleError("未配置管理员账号")
        data = self._request(
            "/api/admin/v1/auth/login",
            method="POST",
            payload={"username": settings["username"], "password": settings["password"]},
        )
        token = (((data or {}).get("data") or {}).get("tokens") or {}).get("accessToken") or ""
        if not token:
            raise GrokConsoleError("登录未返回 accessToken")
        with self._lock:
            self._token = token
            self._token_at = time.time()
        return token

    def _auth_call(self, path: str, method: str = "GET", payload: dict[str, Any] | None = None) -> Any:
        token = self._token if (self._token and time.time() - self._token_at < 600) else self._login()
        try:
            return self._request(path, method=method, payload=payload, token=token)
        except GrokConsoleError as exc:
            if "HTTP 401" not in str(exc):
                raise
            token = self._login()
            return self._request(path, method=method, payload=payload, token=token)

    # ---------- 功能 ----------
    def events(self, since: float = 0.0) -> list[dict[str, Any]]:
        with _EVENT_LOCK:
            return [item for item in _EVENTS if item["ts"] > since]

    def status(self) -> dict[str, Any]:
        settings = self.settings()
        if not settings.get("base_url"):
            return {"configured": False, "ok": False}
        started = time.time()
        try:
            self._login()
            return {"configured": True, "ok": True, "latency_ms": round((time.time() - started) * 1000, 1)}
        except GrokConsoleError as exc:
            return {"configured": True, "ok": False, "error": str(exc)[:200]}

    def summary(self) -> dict[str, Any]:
        data = self._auth_call("/api/admin/v1/accounts/summary")
        return (data or {}).get("data") or {}

    def accounts(self, page: int = 1, page_size: int = 20, keyword: str = "", provider: str = "",
                 auth_status: str = "") -> dict[str, Any]:
        query = [f"page={page}", f"pageSize={page_size}"]
        if keyword:
            query.append(f"q={urllib.parse.quote(keyword)}")
        if provider:
            query.append(f"provider={urllib.parse.quote(provider)}")
        if auth_status:
            query.append(f"authStatus={urllib.parse.quote(auth_status)}")
        data = self._auth_call("/api/admin/v1/accounts?" + "&".join(query))
        return (data or {}).get("data") or {}

    def patch_account(self, account_id: str, payload: dict[str, Any]) -> Any:
        data = self._auth_call(f"/api/admin/v1/accounts/{account_id}", method="PATCH", payload=payload)
        item = (data or {}).get("data") or {}
        return {k: item.get(k) for k in ("id", "enabled", "maxConcurrent", "egressNodeId", "egressAssignmentMode")}

    def delete_account(self, account_id: str, provider: str) -> Any:
        return self._auth_call(f"/api/admin/v1/accounts/{account_id}", method="DELETE",
                               payload={"provider": provider})

    def batch_reset_quota(self, ids: list[str], provider: str, refresh: bool = False) -> Any:
        if refresh:
            return self._auth_call("/api/admin/v1/accounts/batch/refresh-quotas", method="POST",
                                   payload={"ids": ids, "provider": provider})
        results = []
        for account_id in ids:
            try:
                self._auth_call(f"/api/admin/v1/accounts/{account_id}/refresh-quota", method="POST")
                results.append({"id": account_id, "ok": True})
            except GrokConsoleError as exc:
                results.append({"id": account_id, "ok": False, "error": str(exc)[:120]})
        return {"results": results}

    def set_concurrency(self, ids: list[str], value: int) -> Any:
        results = []
        for account_id in ids:
            try:
                item = self.patch_account(account_id, {"maxConcurrent": value})
                results.append({"id": account_id, "ok": True, "maxConcurrent": item.get("maxConcurrent")})
            except GrokConsoleError as exc:
                results.append({"id": account_id, "ok": False, "error": str(exc)[:120]})
        return {"results": results}

    def web_script(self, account_id: str, action: str) -> Any:
        path = {
            "accept_terms": "accept-terms",
            "birth_date": "birth-date",
            "nsfw": "nsfw",
        }.get(action)
        if not path:
            raise GrokConsoleError(f"未知脚本 {action}")
        return self._auth_call(f"/api/admin/v1/accounts/web/{account_id}/{path}", method="POST")

    def egress_create(self, payload: dict[str, Any]) -> Any:
        return self._auth_call("/api/admin/v1/egress-nodes", method="POST", payload=payload)

    def egress_update(self, node_id: str, payload: dict[str, Any]) -> Any:
        return self._auth_call(f"/api/admin/v1/egress-nodes/{node_id}", method="PUT", payload=payload)

    def egress_delete(self, node_id: str) -> Any:
        return self._auth_call(f"/api/admin/v1/egress-nodes/{node_id}", method="DELETE")

    def egress_test(self, ids: list[str]) -> Any:
        return self._auth_call("/api/admin/v1/egress-nodes/test", method="POST", payload={"ids": ids or []})

    def media_generate(self, kind: str, payload: dict[str, Any]) -> Any:
        path = {
            "image": "/v1/images/generations",
            "video": "/v1/videos/generations",
        }.get(kind)
        if not path:
            raise GrokConsoleError(f"未知类型 {kind}")
        settings = self.settings()
        api_key = settings.get("api_key") or ""
        return self._request(path, method="POST", payload=payload, token=api_key, timeout=600)

    def video_status(self, video_id: str) -> Any:
        settings = self.settings()
        api_key = settings.get("api_key") or ""
        return self._request(f"/v1/videos/{video_id}", token=api_key, timeout=120)

    def speech(self, payload: dict[str, Any]) -> bytes:
        settings = self.settings()
        base = (settings.get("base_url") or "").rstrip("/")
        api_key = settings.get("api_key") or ""
        request = urllib.request.Request(
            base + "/v1/audio/speech",
            data=json.dumps(payload).encode(),
            headers={"content-type": "application/json", "authorization": f"Bearer {api_key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise GrokConsoleError(f"HTTP {exc.code}: {detail}") from exc
        except Exception as exc:
            raise GrokConsoleError(f"{type(exc).__name__}: {exc}") from exc

    def chat(self, payload: dict[str, Any]) -> Any:
        settings = self.settings()
        api_key = settings.get("api_key") or ""
        return self._request("/v1/chat/completions", method="POST", payload=payload,
                             token=api_key, timeout=300)

    def keys(self) -> list[dict[str, Any]]:
        data = self._auth_call("/api/admin/v1/client-keys")
        return ((data or {}).get("data") or {}).get("items") or []

    def create_key(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = self._auth_call("/api/admin/v1/client-keys", method="POST", payload=payload)
        return (data or {}).get("data") or {}

    def patch_key(self, key_id: str, payload: dict[str, Any]) -> Any:
        return self._auth_call(f"/api/admin/v1/client-keys/{key_id}", method="PATCH", payload=payload)

    def delete_key(self, key_id: str) -> None:
        self._auth_call(f"/api/admin/v1/client-keys/{key_id}", method="DELETE")

    def reveal_key(self, key_id: str) -> str:
        data = self._auth_call(f"/api/admin/v1/client-keys/{key_id}/secret")
        return str(((data or {}).get("data") or {}).get("secret") or "")

    def audits(self, page: int = 1, page_size: int = 20) -> dict[str, Any]:
        data = self._auth_call(f"/api/admin/v1/request-audits?page={page}&pageSize={page_size}")
        return (data or {}).get("data") or {}

    def egress_nodes(self, page: int = 1, page_size: int = 20) -> dict[str, Any]:
        data = self._auth_call(f"/api/admin/v1/egress-nodes?page={page}&pageSize={page_size}")
        return (data or {}).get("data") or {}

    def media(self, page: int = 1, page_size: int = 20) -> dict[str, Any]:
        data = self._auth_call(f"/api/admin/v1/media/images?page={page}&pageSize={page_size}")
        return (data or {}).get("data") or {}

    def refresh_tokens(self, ids: list[str], provider: str) -> Any:
        settings = self.settings()
        base = (settings.get("base_url") or "").rstrip("/")
        token = self._token if (self._token and time.time() - self._token_at < 600) else self._login()
        request = urllib.request.Request(
            base + "/api/admin/v1/accounts/batch/refresh-tokens",
            data=json.dumps({"ids": ids, "provider": provider}).encode(),
            headers={"content-type": "application/json", "authorization": f"Bearer {token}",
                     "accept": "text/event-stream"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                stream = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            if "Grok Build" in body:
                return {"limited": True, "stream": body[-400:]}
            raise GrokConsoleError(f"HTTP {exc.code}: {body[:200]}") from exc
        except Exception as exc:
            raise GrokConsoleError(f"{type(exc).__name__}: {exc}") from exc
        return {"stream": stream[-2000:]}

    def cleanup_preview(self, provider: str, statuses: list[str]) -> Any:
        return self._auth_call("/api/admin/v1/accounts/cleanup-preview", method="POST",
                               payload={"provider": provider, "statuses": statuses})

    def cleanup(self, provider: str, statuses: list[str]) -> Any:
        return self._auth_call("/api/admin/v1/accounts/cleanup", method="POST",
                               payload={"provider": provider, "statuses": statuses})

    def detect(self, provider: str, ids: list[str] | None = None) -> str:
        payload: dict[str, Any] = {"provider": provider}
        if ids:
            payload["ids"] = ids
        else:
            payload["all"] = True
        settings = self.settings()
        base = (settings.get("base_url") or "").rstrip("/")
        token = self._token if (self._token and time.time() - self._token_at < 600) else self._login()
        request = urllib.request.Request(
            base + "/api/admin/v1/accounts/detect",
            data=json.dumps(payload).encode(),
            headers={"content-type": "application/json", "authorization": f"Bearer {token}",
                     "accept": "text/event-stream"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                return response.read().decode("utf-8", "replace")[-4000:]
        except Exception as exc:
            raise GrokConsoleError(f"{type(exc).__name__}: {exc}") from exc

    def convert_accounts(self, target: str, ids: list[str], strategy: str = "missing") -> str:
        path = {
            "build": "/api/admin/v1/accounts/web/convert-to-build",
            "console": "/api/admin/v1/accounts/web/sync-to-console",
        }.get(target)
        if not path:
            raise GrokConsoleError(f"未知转换目标 {target}")
        settings = self.settings()
        base = (settings.get("base_url") or "").rstrip("/")
        token = self._token if (self._token and time.time() - self._token_at < 600) else self._login()
        payload = {"ids": ids, "strategy": strategy if strategy in ("missing", "all") else "missing"}
        request = urllib.request.Request(
            base + path,
            data=json.dumps(payload).encode(),
            headers={"content-type": "application/json", "authorization": f"Bearer {token}",
                     "accept": "text/event-stream"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                return response.read().decode("utf-8", "replace")[-4000:]
        except Exception as exc:
            raise GrokConsoleError(f"{type(exc).__name__}: {exc}") from exc

    def convert_to_build(self, provider: str, ids: list[str]) -> str:
        settings = self.settings()
        base = (settings.get("base_url") or "").rstrip("/")
        token = self._token if (self._token and time.time() - self._token_at < 600) else self._login()
        request = urllib.request.Request(
            base + "/api/admin/v1/accounts/web/convert-to-build",
            data=json.dumps({"ids": ids, "provider": provider or "grok_web"}).encode(),
            headers={"content-type": "application/json", "authorization": f"Bearer {token}",
                     "accept": "text/event-stream"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                return response.read().decode("utf-8", "replace")[-4000:]
        except Exception as exc:
            raise GrokConsoleError(f"{type(exc).__name__}: {exc}") from exc

    def import_accounts(self, filename: str, content: bytes, provider: str = "") -> Any:
        settings = self.settings()
        base = (settings.get("base_url") or "").rstrip("/")
        token = self._token if (self._token and time.time() - self._token_at < 600) else self._login()
        crlf = chr(13) + chr(10)
        boundary = "----poolsng" + uuid.uuid4().hex
        parts: list[bytes] = []
        if provider:
            parts.append(("--" + boundary + crlf + "Content-Disposition: form-data; name=\"provider\"" + crlf + crlf + provider + crlf).encode())
        parts.append(("--" + boundary + crlf + "Content-Disposition: form-data; name=\"files\"; filename=\"" + filename + "\"" + crlf + "Content-Type: text/plain" + crlf + crlf).encode())
        parts.append(content)
        parts.append((crlf + "--" + boundary + "--" + crlf).encode())
        body = b"".join(parts)
        request = urllib.request.Request(
            base + "/api/admin/v1/accounts/import",
            data=body,
            headers={"content-type": "multipart/form-data; boundary=" + boundary,
                     "authorization": "Bearer " + token, "accept": "text/event-stream"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                return response.read().decode("utf-8", "replace")[-4000:]
        except Exception as exc:
            raise GrokConsoleError(f"{type(exc).__name__}: {exc}") from exc

    def export_formatted(self, provider: str, fmt: str = "grok2api") -> Any:
        raw = self.export_accounts(provider, 500)
        try:
            data = json.loads(raw)
        except Exception:
            return {"format": fmt, "content": raw}
        accounts = data.get("accounts") if isinstance(data, dict) else []
        if fmt == "grok2api":
            return {"format": fmt, "content": raw, "count": len(accounts)}
        items = []
        for account in accounts or []:
            email = str(account.get("email") or "")
            if not email:
                continue
            items.append({
                "email": email,
                "password": str(account.get("password") or ""),
                "access_token": str(account.get("token") or account.get("accessToken") or account.get("access_token") or ""),
                "refresh_token": str(account.get("sso_token") or account.get("refreshToken") or account.get("refresh_token") or ""),
                "id_token": str(account.get("idToken") or account.get("id_token") or ""),
                "cookie": account.get("cloudflare_cookies") or account.get("cookie") or "",
                "tier": account.get("tier") or "",
                "user_id": account.get("user_id") or "",
                "provider": account.get("provider") or provider,
                "name": account.get("name") or "",
                "source": fmt,
            })
        payload = items if fmt == "cpa" else {"accounts": items}
        return {"format": fmt, "content": json.dumps(payload, ensure_ascii=False, indent=2), "count": len(items)}

    def export_accounts(self, provider: str, limit: int = 500) -> str:
        settings = self.settings()
        base = (settings.get("base_url") or "").rstrip("/")
        token = self._token if (self._token and time.time() - self._token_at < 600) else self._login()
        query = urllib.parse.urlencode({"provider": provider, "limit": str(limit), "afterId": "0", "snapshotMaxId": "0"})
        request = urllib.request.Request(f"{base}/api/admin/v1/accounts/export?{query}",
                                         headers={"authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.read().decode("utf-8", "replace")
        except Exception as exc:
            raise GrokConsoleError(f"{type(exc).__name__}: {exc}") from exc

    def device_start(self, email: str, password: str) -> dict[str, Any]:
        data = self._auth_call("/api/admin/v1/accounts/device/start", method="POST",
                               payload={"email": email, "password": password})
        return (data or {}).get("data") or {}

    def device_poll(self, session_id: str) -> dict[str, Any]:
        try:
            data = self._auth_call(f"/api/admin/v1/accounts/device/{session_id}/poll", method="POST", payload={})
        except GrokConsoleError as exc:
            if "429" in str(exc):
                return {"status": "pending", "rateLimited": True}
            raise
        return (data or {}).get("data") or {}

    def dashboard(self, period: str = "24h") -> dict[str, Any]:
        data = self._auth_call(f"/api/admin/v1/dashboard?range={period}")
        return (data or {}).get("data") or {}

    def videos(self, page: int = 1, page_size: int = 24) -> dict[str, Any]:
        data = self._auth_call(f"/api/admin/v1/media/videos?page={page}&pageSize={page_size}")
        return (data or {}).get("data") or {}

    def guard_status(self) -> dict[str, Any]:
        data = self._auth_call("/api/admin/v1/egress-quality-guard")
        return (data or {}).get("data") or {}

    def update_model_route(self, route_id: str, payload: dict[str, Any]) -> Any:
        return self._auth_call(f"/api/admin/v1/models/{route_id}", method="PATCH", payload=payload)

    def model_routes(self) -> list[dict[str, Any]]:
        data = self._auth_call("/api/admin/v1/models")
        return ((data or {}).get("data") or {}).get("items") or []

    def models(self) -> list[dict[str, Any]]:
        settings = self.settings()
        api_key = settings.get("api_key") or ""
        data = self._request("/v1/models", token=api_key)
        return (data or {}).get("data") or []

    def public_call(self, path: str, method: str = "POST", payload: dict[str, Any] | None = None,
                    timeout: int = 300) -> Any:
        """调用 grok2api 的公开 /v1 接口（创作控制台用，凭客户端密钥）。"""
        settings = self.settings()
        if not settings.get("base_url"):
            raise GrokConsoleError("未配置 grok2api 地址")
        api_key = settings.get("api_key") or ""
        if not api_key:
            raise GrokConsoleError("未配置客户端密钥")
        return self._request(path, method=method, payload=payload, token=api_key, timeout=timeout)

    def upload_media(self, filename: str, content: bytes, content_type: str = "application/octet-stream") -> dict[str, Any]:
        """把本地文件上传到 grok2api 的媒体输入区，返回 {fileId, kind, url...}。"""
        settings = self.settings()
        base = (settings.get("base_url") or "").rstrip("/")
        token = self._token if (self._token and time.time() - self._token_at < 600) else self._login()
        crlf = chr(13) + chr(10)
        boundary = "----poolsng" + uuid.uuid4().hex
        head = (
            "--" + boundary + crlf
            + 'Content-Disposition: form-data; name="file"; filename="' + filename.replace('"', "") + '"' + crlf
            + "Content-Type: " + content_type + crlf + crlf
        ).encode()
        tail = (crlf + "--" + boundary + "--" + crlf).encode()
        body = head + content + tail
        request = urllib.request.Request(
            base + "/api/admin/v1/media/inputs/upload", data=body, method="POST",
            headers={"content-type": "multipart/form-data; boundary=" + boundary,
                     "authorization": "Bearer " + token, "accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                payload = json.loads(response.read().decode("utf-8", "replace") or "{}")
                return (payload or {}).get("data") or {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise GrokConsoleError(f"HTTP {exc.code}: {detail}") from exc
        except Exception as exc:
            raise GrokConsoleError(f"{type(exc).__name__}: {exc}") from exc

    def public_open(self, path: str, timeout: int = 300):
        """GET 打开 /v1 接口（媒体等二进制），返回未读取的响应对象。"""
        settings = self.settings()
        if not settings.get("base_url"):
            raise GrokConsoleError("未配置 grok2api 地址")
        api_key = settings.get("api_key") or ""
        if not api_key:
            raise GrokConsoleError("未配置客户端密钥")
        base = (settings.get("base_url") or "").rstrip("/")
        request = urllib.request.Request(
            base + path, method="GET",
            headers={"authorization": f"Bearer {api_key}", "accept": "*/*"},
        )
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise GrokConsoleError(f"HTTP {exc.code}: {detail}") from exc
        except Exception as exc:
            raise GrokConsoleError(f"{type(exc).__name__}: {exc}") from exc

    def public_stream(self, path: str, payload: dict[str, Any], timeout: int = 600):
        """流式调用 /v1 接口，返回未读取的响应对象（供 StreamingResponse 转发）。"""
        settings = self.settings()
        if not settings.get("base_url"):
            raise GrokConsoleError("未配置 grok2api 地址")
        api_key = settings.get("api_key") or ""
        if not api_key:
            raise GrokConsoleError("未配置客户端密钥")
        base = (settings.get("base_url") or "").rstrip("/")
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            base + path, data=data, method="POST",
            headers={"content-type": "application/json", "accept": "text/event-stream",
                     "authorization": f"Bearer {api_key}"},
        )
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise GrokConsoleError(f"HTTP {exc.code}: {detail}") from exc
        except Exception as exc:
            raise GrokConsoleError(f"{type(exc).__name__}: {exc}") from exc


grok_console = GrokConsole()

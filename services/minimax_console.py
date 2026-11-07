"""MiniMax 控制台：对接 minimax2api 的 admin API。

定位与 Grok 控制台一致——minimax2api 是独立部署的号池网关，本模块只是
在 chatgpt-pool-ng 的控制台里把它管理起来：

- 账号池（列表 / 导入导出 / 批量操作 / 探活 / 额度 / 签到 / 积分）
- 客户端密钥、模型目录、审计记录、签到总览、媒体库、系统设置、浏览器登录

账号本身仍然由 minimax2api 持有，本模块不迁移、不复制账号数据。

minimax2api 默认只监听 VPS 上的 127.0.0.1:8095，所以这里内置 SSH 隧道：
隧道不通时按需拉一条 `ssh -N -L <local>:127.0.0.1:<remote>`。
"""
from __future__ import annotations

import json
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

DATA_FILE = Path(__file__).resolve().parents[1] / "data" / "minimax_console.json"

DEFAULT_BASE = "http://127.0.0.1:18095"
DEFAULT_SSH_HOST = "root@47.80.60.216"
DEFAULT_REMOTE_PORT = 8095
DEFAULT_LOCAL_PORT = 18095

# 登录态有效期：保守取 30 分钟
TOKEN_TTL = 1800


class MinimaxConsoleError(RuntimeError):
    """上游 minimax2api 不可用或返回错误。"""


def _default_settings() -> dict[str, Any]:
    return {
        "base_url": DEFAULT_BASE,
        "username": "",
        "password": "",
        "auto_tunnel": True,
        "ssh_host": DEFAULT_SSH_HOST,
        "ssh_remote_port": DEFAULT_REMOTE_PORT,
        "ssh_local_port": DEFAULT_LOCAL_PORT,
    }


class MinimaxConsole:
    """minimax2api admin API 的客户端。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._token: str = ""
        self._token_at: float = 0.0
        self._tunnel: subprocess.Popen | None = None

    # ---------- 设置 ----------
    def _load(self) -> dict[str, Any]:
        settings = _default_settings()
        if DATA_FILE.exists():
            try:
                raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    settings.update({k: v for k, v in raw.items() if k in settings})
            except Exception:
                pass
        return settings

    def _save(self, settings: dict[str, Any]) -> None:
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        DATA_FILE.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")

    def settings(self) -> dict[str, Any]:
        return self._load()

    def public_settings(self) -> dict[str, Any]:
        """给前端看的设置，密码打码。"""
        settings = self._load()
        password = str(settings.get("password") or "")
        settings["password"] = ("•" * 6 + password[-4:]) if len(password) >= 4 else ("•" * len(password))
        settings["has_password"] = bool(password)
        settings["tunnel_running"] = self._tunnel_alive()
        return settings

    def save_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        settings = self._load()
        for key in ("base_url", "username", "password", "ssh_host"):
            if key in payload:
                value = str(payload.get(key) or "").strip()
                if key == "password" and value.startswith("•"):
                    continue  # 前端回传的是打码值，保留原密码
                settings[key] = value
        for key in ("auto_tunnel",):
            if key in payload:
                settings[key] = bool(payload.get(key))
        for key in ("ssh_remote_port", "ssh_local_port"):
            if key in payload:
                try:
                    settings[key] = max(1, int(payload.get(key)))
                except (TypeError, ValueError):
                    pass
        self._save(settings)
        return self.public_settings()

    # ---------- 隧道 ----------
    def _tunnel_alive(self) -> bool:
        return self._tunnel is not None and self._tunnel.poll() is None

    def _port_open(self, port: int) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1.0):
                return True
        except OSError:
            return False

    def start_tunnel(self) -> bool:
        settings = self._load()
        host = str(settings.get("ssh_host") or "").strip()
        remote_port = int(settings.get("ssh_remote_port") or DEFAULT_REMOTE_PORT)
        local_port = int(settings.get("ssh_local_port") or DEFAULT_LOCAL_PORT)
        if not host:
            raise MinimaxConsoleError("未配置 SSH 主机，无法建立隧道")
        with self._lock:
            if self._tunnel_alive():
                return True
            cmd = [
                "ssh", "-N", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
                "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3",
                "-L", f"{local_port}:127.0.0.1:{remote_port}", host,
            ]
            try:
                self._tunnel = subprocess.Popen(
                    cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            except Exception as exc:
                self._tunnel = None
                raise MinimaxConsoleError(f"无法启动 SSH 隧道: {exc}") from exc
        for _ in range(20):
            if self._port_open(local_port):
                return True
            time.sleep(0.5)
        raise MinimaxConsoleError(f"SSH 隧道未能在本地端口 {local_port} 上就绪")

    def stop_tunnel(self) -> None:
        with self._lock:
            if self._tunnel is not None and self._tunnel.poll() is None:
                self._tunnel.terminate()
                try:
                    self._tunnel.wait(timeout=3)
                except Exception:
                    self._tunnel.kill()
            self._tunnel = None

    # ---------- 请求 ----------
    def _base(self) -> str:
        settings = self._load()
        return str(settings.get("base_url") or DEFAULT_BASE).rstrip("/")

    def _local_port(self) -> int:
        settings = self._load()
        return int(settings.get("ssh_local_port") or DEFAULT_LOCAL_PORT)

    def _ensure_ready(self) -> str:
        base = self._base()
        if self._port_open(self._local_port()):
            return base
        settings = self._load()
        if settings.get("auto_tunnel", True):
            self.start_tunnel()
            return base
        raise MinimaxConsoleError(f"minimax2api 不可达（{base}），请检查隧道或服务状态")

    def _login(self) -> str:
        settings = self._load()
        username = str(settings.get("username") or "").strip()
        password = str(settings.get("password") or "")
        if not username or not password:
            raise MinimaxConsoleError("未配置 minimax2api 管理员账号")
        base = self._ensure_ready()
        payload = json.dumps({"username": username, "password": password}).encode("utf-8")
        request = urllib.request.Request(
            f"{base}/admin/api/auth/login",
            data=payload,
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                body = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:200]
            raise MinimaxConsoleError(f"登录失败（HTTP {exc.code}）：{detail}") from exc
        except Exception as exc:
            raise MinimaxConsoleError(f"登录失败：{type(exc).__name__}: {exc}") from exc
        try:
            token = str((json.loads(body) or {}).get("token") or "")
        except json.JSONDecodeError as exc:
            raise MinimaxConsoleError("登录响应不是合法 JSON") from exc
        if not token:
            raise MinimaxConsoleError("登录未返回 token")
        with self._lock:
            self._token = token
            self._token_at = time.time()
        return token

    def _auth_token(self) -> str:
        with self._lock:
            if self._token and (time.time() - self._token_at) < TOKEN_TTL:
                return self._token
        return self._login()

    def _request(self, path: str, method: str = "GET", payload: Any = None,
                 timeout: int = 60, retry_auth: bool = True) -> Any:
        """调用 minimax2api 的 admin API。path 以 /admin/api 开头。"""
        base = self._ensure_ready()
        token = self._auth_token()
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"accept": "application/json", "authorization": f"Bearer {token}"}
        if data is not None:
            headers["content-type"] = "application/json"
        request = urllib.request.Request(f"{base}{path}", data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8", "replace")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            if exc.code in (401, 403) and retry_auth:
                with self._lock:
                    self._token = ""
                return self._request(path, method, payload, timeout, retry_auth=False)
            raise MinimaxConsoleError(f"HTTP {exc.code}: {detail}") from exc
        except Exception as exc:
            raise MinimaxConsoleError(f"{type(exc).__name__}: {exc}") from exc

    # ---------- 业务接口 ----------
    def dashboard(self) -> dict[str, Any]:
        return self._request("/admin/api/dashboard")

    def me(self) -> dict[str, Any]:
        return self._request("/admin/api/auth/me")

    def change_password(self, old_password: str, new_password: str) -> dict[str, Any]:
        return self._request("/admin/api/auth/password", method="POST",
                             payload={"oldPassword": old_password, "newPassword": new_password})

    def accounts(self, page: int = 1, page_size: int = 20, q: str = "") -> dict[str, Any]:
        query = f"?page={max(1, page)}&page_size={max(1, min(page_size, 200))}"
        if q:
            query += f"&q={urllib.parse.quote(q)}"
        return self._request(f"/admin/api/accounts{query}")

    def account_groups(self) -> dict[str, Any]:
        return self._request("/admin/api/accounts/groups")

    def create_account(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("/admin/api/accounts", method="POST", payload=payload)

    def update_account(self, account_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request(f"/admin/api/accounts/{account_id}", method="PATCH", payload=payload)

    def delete_account(self, account_id: str) -> dict[str, Any]:
        return self._request(f"/admin/api/accounts/{account_id}", method="DELETE")

    def export_accounts(self, fmt: str = "json", limit: int = 500) -> dict[str, Any]:
        return self._request(f"/admin/api/accounts/export?format={urllib.parse.quote(fmt)}&limit={max(1, limit)}")

    def import_accounts(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("/admin/api/accounts/import", method="POST", payload=payload, timeout=300)

    def account_ops(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("/admin/api/accounts/batch", method="POST", payload={"action": action, **payload})

    def account_action(self, account_id: str, action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._request(f"/admin/api/accounts/{account_id}/{action}", method="POST", payload=payload or {})

    def probe_all(self) -> dict[str, Any]:
        return self._request("/admin/api/accounts/probe-all", method="POST", payload={}, timeout=600)

    def quota_all(self) -> dict[str, Any]:
        return self._request("/admin/api/accounts/quota-all", method="POST", payload={}, timeout=600)

    def cleanup_accounts(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._request("/admin/api/accounts/cleanup", method="POST", payload=payload or {}, timeout=600)

    def client_keys(self) -> dict[str, Any]:
        return self._request("/admin/api/client-keys")

    def create_client_key(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("/admin/api/client-keys", method="POST", payload=payload)

    def update_client_key(self, key_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request(f"/admin/api/client-keys/{key_id}", method="PATCH", payload=payload)

    def delete_client_key(self, key_id: str) -> dict[str, Any]:
        return self._request(f"/admin/api/client-keys/{key_id}", method="DELETE")

    def models(self) -> dict[str, Any]:
        return self._request("/admin/api/models")

    def update_model(self, model_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request(f"/admin/api/models/{model_id}", method="PATCH", payload=payload)

    def audits(self, page: int = 1, page_size: int = 20) -> dict[str, Any]:
        return self._request(f"/admin/api/audits?page={max(1, page)}&page_size={max(1, min(page_size, 200))}")

    def audit_detail(self, audit_id: str) -> dict[str, Any]:
        return self._request(f"/admin/api/audits/{audit_id}")

    def clear_audits(self) -> dict[str, Any]:
        return self._request("/admin/api/audits", method="DELETE")

    def signin_overview(self) -> dict[str, Any]:
        return self._request("/admin/api/signin")

    def signin_run(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._request("/admin/api/signin/run", method="POST", payload=payload or {}, timeout=600)

    def gallery(self) -> dict[str, Any]:
        return self._request("/admin/api/gallery")

    def remote_settings(self) -> dict[str, Any]:
        return self._request("/admin/api/settings")

    def save_remote_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("/admin/api/settings", method="PUT", payload=payload)

    def browser_login_start(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("/admin/api/browser-login", method="POST", payload=payload)

    def browser_login_poll(self, job_id: str) -> dict[str, Any]:
        return self._request(f"/admin/api/browser-login/{job_id}")

    def browser_login_cancel(self, job_id: str) -> dict[str, Any]:
        return self._request(f"/admin/api/browser-login/{job_id}", method="DELETE")

    def status(self) -> dict[str, Any]:
        """面板顶部用的一行状态。"""
        info: dict[str, Any] = {
            "base_url": self._base(),
            "tunnel_running": self._tunnel_alive(),
            "reachable": False,
            "configured": bool(self._load().get("username")),
            "error": "",
        }
        try:
            self._ensure_ready()
            self._auth_token()
            info["reachable"] = True
        except Exception as exc:
            info["error"] = f"{type(exc).__name__}: {exc}"[:200]
        return info


minimax_console = MinimaxConsole()

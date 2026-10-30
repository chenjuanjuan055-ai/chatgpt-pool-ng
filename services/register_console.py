"""注册控制台：对接本机 grok-register（默认 127.0.0.1:8092）的 HTTP API。"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DATA_FILE = Path(__file__).resolve().parents[1] / "data" / "register_console.json"
POOL_FILE = Path(__file__).resolve().parents[1] / "data" / "register_pool.txt"
DEFAULT_BASE = "http://127.0.0.1:8092"

# 控制台可编辑的 grok-register 配置项（不含任何本机私有值，全部由使用者填写）
EDITABLE_KEYS = (
    # 邮箱
    "email_provider", "defaultDomains",
    "cloudflare_api_base", "cloudflare_api_key", "cloudflare_auth_mode",
    "cloudflare_path_domains", "cloudflare_path_accounts", "cloudflare_path_token", "cloudflare_path_messages",
    "duckmail_api_key",
    "yyds_api_key", "yyds_jwt",
    "outlook_accounts_file",
    "cloudmail_api_base", "cloudmail_public_token", "cloudmail_domains", "cloudmail_path_messages",
    "mailtm_api_base", "mailtm_domain",
    # 代理
    "proxy_mode", "proxy", "proxy_fallback",
    "proxy_pool_file", "proxy_pool_subscription_url", "proxy_pool_subscription_proxy",
    "proxy_pool_endpoint_mode", "proxy_pool_max_concurrent_per_node",
    "proxy_pool_probe_interval_sec", "proxy_pool_preflight_enabled",
    # 任务
    "register_count", "multi_thread_enabled", "multi_thread_workers",
    "enable_nsfw", "user_agent",
    # CPA
    "cpa_export_enabled", "cpa_auth_dir", "cpa_headless", "cpa_force_standalone",
    "cpa_mint_timeout_sec", "cpa_base_url", "cpa_mint_cookie_inject",
    # 自动入池
    "grok2api_auto_add_local", "grok2api_local_token_file", "grok2api_pool_name",
    "grok2api_auto_add_remote", "grok2api_remote_base", "grok2api_remote_app_key",
    "grok2api_remote_admin_username", "grok2api_remote_admin_password",
)

SECRET_HINTS = ("_key", "_token", "_password", "_jwt", "_secret")
_MASK = "••••••"


def is_secret(key: str) -> bool:
    lowered = key.lower()
    if "_path_" in lowered or lowered.endswith(("_file", "_dir", "_base", "_url", "_domains")):
        return False
    return any(hint in lowered for hint in SECRET_HINTS)


def mask_config(cfg: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for key, value in cfg.items():
        if key not in EDITABLE_KEYS:
            continue
        if is_secret(key) and isinstance(value, str) and value:
            out[key] = _MASK + value[-4:] if len(value) > 4 else _MASK
        else:
            out[key] = value
    return out


def strip_masked(updates: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in updates.items()
        if key in EDITABLE_KEYS and not (isinstance(value, str) and value.startswith(_MASK))
    }


class RegisterConsoleError(RuntimeError):
    pass


class RegisterConsole:
    def settings(self) -> dict[str, str]:
        if not DATA_FILE.exists():
            return {"base_url": DEFAULT_BASE}
        try:
            data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            return {"base_url": str(data.get("base_url") or DEFAULT_BASE).rstrip("/")}
        except Exception:
            return {"base_url": DEFAULT_BASE}

    def save_settings(self, payload: dict[str, Any]) -> dict[str, str]:
        current = self.settings()
        base_url = str(payload.get("base_url") or current["base_url"]).strip().rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            raise RegisterConsoleError("注册服务地址必须以 http:// 或 https:// 开头")
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        DATA_FILE.write_text(json.dumps({"base_url": base_url}, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        return {"base_url": base_url}

    def _request(self, path: str, method: str = "GET", payload: dict[str, Any] | None = None,
                 timeout: int = 30) -> Any:
        base = self.settings()["base_url"]
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"accept": "application/json", "origin": base, "referer": base + "/"}
        if data is not None:
            headers["content-type"] = "application/json"
        request = urllib.request.Request(base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8", "replace")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise RegisterConsoleError(f"HTTP {exc.code}: {detail}") from exc
        except Exception as exc:
            raise RegisterConsoleError(f"{type(exc).__name__}: {exc}") from exc

    def status(self) -> dict[str, Any]:
        return self._request("/api/status")

    def logs(self, after: int = 0) -> dict[str, Any]:
        data = self._request("/api/logs")
        entries = [e for e in (data.get("entries") or []) if int(e.get("seq") or 0) > after]
        return {"latest": data.get("latest", 0), "entries": entries[-300:], "ok": data.get("ok", True)}

    def config(self) -> dict[str, Any]:
        return self._request("/api/config").get("config") or {}

    def update_config(self, updates: dict[str, Any]) -> dict[str, Any]:
        return self._request("/api/config", method="PUT", payload=updates).get("config") or {}

    def start(self) -> dict[str, Any]:
        return self._request("/api/start", method="POST", timeout=60)

    def stop(self) -> dict[str, Any]:
        return self._request("/api/stop", method="POST", timeout=60)

    def pool_reload(self) -> dict[str, Any]:
        return self._request("/api/proxy-pool/reload", method="POST", timeout=60)

    def pool_status(self) -> dict[str, Any]:
        return self._request("/api/proxy-pool/status")

    def pool_test(self) -> dict[str, Any]:
        return self._request("/api/proxy-pool/test", method="POST", timeout=180)

    # ---------- 代理池内容（由控制台托管，路径随控制台走，便于开源分发） ----------
    def pool_content(self) -> dict[str, Any]:
        lines: list[str] = []
        if POOL_FILE.exists():
            lines = [ln.strip() for ln in POOL_FILE.read_text(encoding="utf-8").splitlines() if ln.strip()]
        return {"lines": lines, "path": str(POOL_FILE)}

    def save_pool(self, lines: list[str], apply: bool = True) -> dict[str, Any]:
        cleaned = [str(ln).strip() for ln in (lines or []) if str(ln).strip()]
        POOL_FILE.parent.mkdir(parents=True, exist_ok=True)
        POOL_FILE.write_text("\n".join(cleaned) + ("\n" if cleaned else ""), encoding="utf-8")
        cfg: dict[str, Any] = {}
        if apply and cleaned:
            cfg = self.update_config({"proxy_mode": "pool", "proxy_pool_file": str(POOL_FILE)})
            self.pool_reload()
        return {"lines": cleaned, "path": str(POOL_FILE), "config": mask_config(cfg) if cfg else {}}


register_console = RegisterConsole()

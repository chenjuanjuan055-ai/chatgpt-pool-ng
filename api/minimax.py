"""MiniMax 控制台 API。

把 minimax2api（独立部署的号池网关）纳进本控制台统一管理，
形态与 Grok 控制台一致。账号池数据仍留在 minimax2api 那边，这里只做代理与展示。
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from starlette.concurrency import run_in_threadpool

from api.support import require_admin
from services.minimax_console import MinimaxConsoleError, minimax_console

# minimax2api 的 admin API 用 Bearer token，前端不直接持有，统一由服务端转发
# 动作名白名单，避免把任意路径拼到上游
ACCOUNT_ACTIONS = {"probe", "quota", "signin", "credit"}


def _guard(error: Exception) -> HTTPException:
    return HTTPException(status_code=502, detail={"error": str(error)[:300]})


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/minimax/settings")
    async def get_settings(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"settings": minimax_console.public_settings()}

    @router.post("/api/minimax/settings")
    async def save_settings(body: dict, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"settings": minimax_console.save_settings(body)}

    @router.get("/api/minimax/status")
    async def status(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(minimax_console.status)
        except Exception as exc:
            raise _guard(exc) from exc

    @router.get("/api/minimax/dashboard")
    async def dashboard(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(minimax_console.dashboard)
        except Exception as exc:
            raise _guard(exc) from exc

    @router.get("/api/minimax/accounts")
    async def accounts(page: int = 1, page_size: int = 20, q: str = "",
                       authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(minimax_console.accounts, page, page_size, q)
        except Exception as exc:
            raise _guard(exc) from exc

    @router.get("/api/minimax/accounts/groups")
    async def account_groups(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(minimax_console.account_groups)
        except Exception as exc:
            raise _guard(exc) from exc

    @router.post("/api/minimax/accounts/batch")
    async def accounts_batch(body: dict, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        action = str(body.get("action") or "").strip()
        if not action:
            raise HTTPException(status_code=400, detail={"error": "action 必填"})
        try:
            return await run_in_threadpool(minimax_console.account_ops, action, body)
        except Exception as exc:
            raise _guard(exc) from exc

    @router.post("/api/minimax/accounts/{account_id}/{action}")
    async def account_action(account_id: str, action: str, body: dict,
                             authorization: str | None = Header(default=None)):
        require_admin(authorization)
        if action not in ACCOUNT_ACTIONS:
            raise HTTPException(status_code=400, detail={"error": f"不支持的操作 {action}"})
        try:
            return await run_in_threadpool(
                minimax_console.account_action, account_id, action, body,
            )
        except Exception as exc:
            raise _guard(exc) from exc

    @router.get("/api/minimax/keys")
    async def keys(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(minimax_console.client_keys)
        except Exception as exc:
            raise _guard(exc) from exc

    @router.get("/api/minimax/models")
    async def models(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(minimax_console.models)
        except Exception as exc:
            raise _guard(exc) from exc

    @router.get("/api/minimax/audits")
    async def audits(page: int = 1, page_size: int = 20,
                     authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(minimax_console.audits, page, page_size)
        except Exception as exc:
            raise _guard(exc) from exc

    @router.get("/api/minimax/signin")
    async def signin(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(minimax_console.signin_overview)
        except Exception as exc:
            raise _guard(exc) from exc

    @router.post("/api/minimax/signin/run")
    async def signin_run(body: dict, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(minimax_console.signin_run, body)
        except Exception as exc:
            raise _guard(exc) from exc

    @router.get("/api/minimax/gallery")
    async def gallery(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(minimax_console.gallery)
        except Exception as exc:
            raise _guard(exc) from exc

    @router.post("/api/minimax/browser-login")
    async def browser_login_start(body: dict, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(minimax_console.browser_login_start, body)
        except Exception as exc:
            raise _guard(exc) from exc

    @router.get("/api/minimax/browser-login/{job_id}")
    async def browser_login_poll(job_id: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(minimax_console.browser_login_poll, job_id)
        except Exception as exc:
            raise _guard(exc) from exc

    @router.post("/api/minimax/tunnel/start")
    async def tunnel_start(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            ok = await run_in_threadpool(minimax_console.start_tunnel)
            return {"ok": bool(ok), "tunnel_running": minimax_console._tunnel_alive()}
        except Exception as exc:
            raise _guard(exc) from exc

    @router.post("/api/minimax/tunnel/stop")
    async def tunnel_stop(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        minimax_console.stop_tunnel()
        return {"ok": True, "tunnel_running": False}

    return router

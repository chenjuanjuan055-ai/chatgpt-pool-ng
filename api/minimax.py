"""MiniMax 控制台 API。

把 minimax2api（独立部署的号池网关）纳进本控制台统一管理，
形态与 Grok 控制台一致。账号池数据仍留在 minimax2api 那边，这里只做代理与展示。
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query
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
    async def accounts(
        page: int = 1,
        # 对齐 minimax2api 的驼峰参数名：pageSize / search / sortBy / sortOrder。
        # 不用 alias 的话 FastAPI 会把前端传来的 pageSize 当未知参数丢掉，
        # 静默退回默认值——搜索和分页看起来"能用"但其实没生效。
        page_size: int = Query(default=20, alias="pageSize", ge=1, le=500),
        q: str = Query(default="", alias="search"),
        status: str = "",
        kind: str = "",
        group: str = "",
        sort_by: str = Query(default="", alias="sortBy"),
        sort_order: str = Query(default="", alias="sortOrder"),
        authorization: str | None = Header(default=None),
    ):
        require_admin(authorization)
        try:
            return await run_in_threadpool(
                minimax_console.accounts, page, page_size, q, status, kind, group, sort_by, sort_order,
            )
        except Exception as exc:
            raise _guard(exc) from exc

    @router.get("/api/minimax/accounts/groups")
    async def account_groups(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(minimax_console.account_groups)
        except Exception as exc:
            raise _guard(exc) from exc

    @router.post("/api/minimax/accounts")
    async def create_account(body: dict, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(minimax_console.create_account, body)
        except Exception as exc:
            raise _guard(exc) from exc

    @router.patch("/api/minimax/accounts/{account_id}")
    async def update_account(account_id: str, body: dict, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(minimax_console.update_account, account_id, body)
        except Exception as exc:
            raise _guard(exc) from exc

    @router.delete("/api/minimax/accounts/{account_id}")
    async def delete_account(account_id: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(minimax_console.delete_account, account_id)
        except Exception as exc:
            raise _guard(exc) from exc

    @router.get("/api/minimax/accounts/export")
    async def export_accounts(format: str = "json", limit: int = 500,
                              authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(minimax_console.export_accounts, format, limit)
        except Exception as exc:
            raise _guard(exc) from exc

    @router.post("/api/minimax/accounts/import")
    async def import_accounts(body: dict, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(minimax_console.import_accounts, body)
        except Exception as exc:
            raise _guard(exc) from exc

    @router.post("/api/minimax/accounts/probe-all")
    async def probe_all(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(minimax_console.probe_all)
        except Exception as exc:
            raise _guard(exc) from exc

    @router.post("/api/minimax/accounts/quota-all")
    async def quota_all(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(minimax_console.quota_all)
        except Exception as exc:
            raise _guard(exc) from exc

    @router.post("/api/minimax/accounts/cleanup")
    async def cleanup_accounts(body: dict, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(minimax_console.cleanup_accounts, body)
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

    @router.post("/api/minimax/keys")
    async def create_key(body: dict, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(minimax_console.create_client_key, body)
        except Exception as exc:
            raise _guard(exc) from exc

    @router.patch("/api/minimax/keys/{key_id}")
    async def update_key(key_id: str, body: dict, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(minimax_console.update_client_key, key_id, body)
        except Exception as exc:
            raise _guard(exc) from exc

    @router.delete("/api/minimax/keys/{key_id}")
    async def delete_key(key_id: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(minimax_console.delete_client_key, key_id)
        except Exception as exc:
            raise _guard(exc) from exc

    @router.get("/api/minimax/models")
    async def models(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(minimax_console.models)
        except Exception as exc:
            raise _guard(exc) from exc

    @router.patch("/api/minimax/models/{model_id}")
    async def update_model(model_id: str, body: dict, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(minimax_console.update_model, model_id, body)
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

    @router.get("/api/minimax/audits/{audit_id}")
    async def audit_detail(audit_id: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(minimax_console.audit_detail, audit_id)
        except Exception as exc:
            raise _guard(exc) from exc

    @router.delete("/api/minimax/audits")
    async def clear_audits(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(minimax_console.clear_audits)
        except Exception as exc:
            raise _guard(exc) from exc

    @router.get("/api/minimax/upstream-settings")
    async def upstream_settings(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(minimax_console.remote_settings)
        except Exception as exc:
            raise _guard(exc) from exc

    @router.put("/api/minimax/upstream-settings")
    async def save_upstream_settings(body: dict, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(minimax_console.save_remote_settings, body)
        except Exception as exc:
            raise _guard(exc) from exc

    @router.post("/api/minimax/password")
    async def change_password(body: dict, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        old_password = str(body.get("oldPassword") or body.get("old_password") or "")
        new_password = str(body.get("newPassword") or body.get("new_password") or "")
        if not old_password or not new_password:
            raise HTTPException(status_code=400, detail={"error": "原密码与新密码必填"})
        try:
            return await run_in_threadpool(minimax_console.change_password, old_password, new_password)
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

    @router.delete("/api/minimax/browser-login/{job_id}")
    async def browser_login_cancel(job_id: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(minimax_console.browser_login_cancel, job_id)
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

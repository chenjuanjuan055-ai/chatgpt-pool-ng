"""Grok 注册控制台 API：驱动本机 grok-register（使用者自行安装并配置）。"""
from __future__ import annotations

from fastapi import APIRouter, Body, Header, HTTPException
from fastapi.concurrency import run_in_threadpool

from api.support import require_admin
from services.register_console import (
    RegisterConsoleError,
    mask_config,
    register_console,
    strip_masked,
)


def _guard(error: Exception) -> HTTPException:
    return HTTPException(status_code=502, detail={"error": str(error)[:300]})


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/register/settings")
    async def get_settings(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        settings = register_console.settings()
        try:
            await run_in_threadpool(register_console.status)
            reachable = True
            detail = ""
        except RegisterConsoleError as exc:
            reachable = False
            detail = str(exc)[:200]
        return {"settings": settings, "reachable": reachable, "detail": detail}

    @router.put("/api/register/settings")
    async def put_settings(payload: dict = Body(...), authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return {"settings": register_console.save_settings(payload or {})}
        except RegisterConsoleError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @router.get("/api/register/status")
    async def status(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            job = await run_in_threadpool(register_console.status)
            cfg = await run_in_threadpool(register_console.config)
        except RegisterConsoleError as exc:
            raise _guard(exc) from exc
        return {"job": job, "config": mask_config(cfg)}

    @router.get("/api/register/logs")
    async def logs(after: int = 0, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(register_console.logs, after)
        except RegisterConsoleError as exc:
            raise _guard(exc) from exc

    @router.put("/api/register/config")
    async def update_config(payload: dict = Body(...), authorization: str | None = Header(default=None)):
        require_admin(authorization)
        updates = strip_masked(payload or {})
        if not updates:
            raise HTTPException(status_code=400, detail={"error": "没有需要更新的配置"})
        try:
            cfg = await run_in_threadpool(register_console.update_config, updates)
        except RegisterConsoleError as exc:
            raise _guard(exc) from exc
        return {"config": mask_config(cfg)}

    @router.post("/api/register/start")
    async def start(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(register_console.start)
        except RegisterConsoleError as exc:
            raise _guard(exc) from exc

    @router.post("/api/register/stop")
    async def stop(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(register_console.stop)
        except RegisterConsoleError as exc:
            raise _guard(exc) from exc

    @router.post("/api/register/pool/reload")
    async def pool_reload(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(register_console.pool_reload)
        except RegisterConsoleError as exc:
            raise _guard(exc) from exc

    @router.get("/api/register/pool/content")
    async def pool_content(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return register_console.pool_content()

    @router.put("/api/register/pool/content")
    async def pool_save(payload: dict = Body(...), authorization: str | None = Header(default=None)):
        require_admin(authorization)
        lines = payload.get("lines")
        if isinstance(lines, str):
            lines = lines.splitlines()
        if not isinstance(lines, list):
            raise HTTPException(status_code=400, detail={"error": "lines 必须是节点列表或文本"})
        try:
            return await run_in_threadpool(register_console.save_pool, lines, bool(payload.get("apply", True)))
        except RegisterConsoleError as exc:
            raise _guard(exc) from exc

    @router.post("/api/register/pool/test")
    async def pool_test(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            data = await run_in_threadpool(register_console.pool_test)
        except RegisterConsoleError as exc:
            raise _guard(exc) from exc
        nodes = [
            {
                "proxy": n.get("proxy"),
                "probe_status": n.get("probe_status"),
                "exit_ip": n.get("exit_ip"),
                "latency_ms": n.get("probe_latency_ms"),
                "error": (n.get("probe_error") or "")[:120],
            }
            for n in (data.get("nodes") or [])
        ]
        return {"nodes": nodes, "capacity": data.get("capacity")}

    return router

"""Grok 控制台 API（独立于 GPT 号池）。"""
from __future__ import annotations

import time

from fastapi import APIRouter, File, Form, Header, HTTPException, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from api.support import require_admin
from services.grok_console import GrokConsoleError, grok_console, record_event


class GrokSettingsPayload(BaseModel):
    base_url: str | None = None
    username: str | None = None
    password: str | None = None
    api_key: str | None = None


class GrokKeyPayload(BaseModel):
    name: str = ""
    rpm_limit: int | None = None
    max_concurrent: int | None = None
    billing_limit_usd: float | None = None
    allowed_models: list[str] | None = None


def _guard(error: Exception) -> HTTPException:
    return HTTPException(status_code=502, detail={"error": str(error)[:300]})


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/grok/settings")
    async def get_settings(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"settings": grok_console.public_settings()}

    @router.post("/api/grok/settings")
    async def save_settings(body: GrokSettingsPayload, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"settings": await run_in_threadpool(grok_console.save_settings, body.model_dump())}

    @router.get("/api/grok/events")
    async def events(since: float = 0.0, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"items": grok_console.events(since), "now": time.time()}

    @router.get("/api/grok/status")
    async def status(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(grok_console.status)
        except GrokConsoleError as exc:
            raise _guard(exc) from exc

    @router.get("/api/grok/overview")
    async def overview(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            summary = await run_in_threadpool(grok_console.summary)
            keys = await run_in_threadpool(grok_console.keys)
        except GrokConsoleError as exc:
            raise _guard(exc) from exc
        return {"summary": summary, "keys": len(keys)}

    @router.get("/api/grok/accounts")
    async def accounts(page: int = 1, page_size: int = 20, q: str = "", provider: str = "",
                       auth_status: str = "", authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            data = await run_in_threadpool(
                grok_console.accounts, page, page_size, q, provider, auth_status
            )
        except GrokConsoleError as exc:
            raise _guard(exc) from exc
        return data

    @router.post("/api/grok/accounts/{account_id}/toggle")
    async def toggle_account(account_id: str, body: dict, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            data = await run_in_threadpool(grok_console.patch_account, account_id, {"enabled": bool(body.get("enabled"))})
        except GrokConsoleError as exc:
            record_event("error", f"账号 {account_id} 启停失败", str(exc))
            raise _guard(exc) from exc
        record_event("account", f"账号 {account_id} → {'启用' if body.get('enabled') else '停用'}")
        return {"ok": True, "account": data}

    @router.post("/api/grok/accounts/batch")
    async def batch_accounts(body: dict, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        action = str(body.get("action") or "")
        ids = [str(item) for item in (body.get("ids") or [])]
        provider = str(body.get("provider") or "")
        if not ids:
            raise HTTPException(status_code=400, detail={"error": "ids 为空"})
        try:
            if action == "reset_quota":
                await run_in_threadpool(grok_console.batch_reset_quota, ids, provider, False)
            elif action == "refresh_quota":
                await run_in_threadpool(grok_console.batch_reset_quota, ids, provider, True)
            elif action in ("enable", "disable"):
                enabled = action == "enable"
                for account_id in ids:
                    await run_in_threadpool(grok_console.patch_account, account_id, {"enabled": enabled})
            elif action == "delete":
                for account_id in ids:
                    await run_in_threadpool(grok_console.delete_account, account_id, provider)
            else:
                raise HTTPException(status_code=400, detail={"error": f"未知操作 {action}"})
        except GrokConsoleError as exc:
            record_event("error", f"批量 {action} 失败", str(exc))
            raise _guard(exc) from exc
        record_event("batch", f"批量 {action}：{len(ids)} 个账号")
        return {"ok": True, "count": len(ids)}

    @router.post("/api/grok/accounts/device/start")
    async def device_start(body: dict, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        email = str(body.get("email") or "").strip()
        password = str(body.get("password") or "")
        if not email:
            raise HTTPException(status_code=400, detail={"error": "email 必填"})
        try:
            return await run_in_threadpool(grok_console.device_start, email, password)
        except GrokConsoleError as exc:
            raise _guard(exc) from exc

    @router.post("/api/grok/accounts/device/poll")
    async def device_poll(body: dict, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        session_id = str(body.get("session_id") or "").strip()
        if not session_id:
            raise HTTPException(status_code=400, detail={"error": "session_id 必填"})
        try:
            return await run_in_threadpool(grok_console.device_poll, session_id)
        except GrokConsoleError as exc:
            raise _guard(exc) from exc

    @router.post("/api/grok/accounts/ops")
    async def account_ops(body: dict, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        action = str(body.get("action") or "")
        provider = str(body.get("provider") or "")
        ids = [str(item) for item in (body.get("ids") or [])]
        statuses = [str(item) for item in (body.get("statuses") or [])]
        try:
            if action == "refresh_tokens":
                if not ids:
                    raise HTTPException(status_code=400, detail={"error": "ids 为空"})
                return await run_in_threadpool(grok_console.refresh_tokens, ids, provider)
            if action == "cleanup_preview":
                return await run_in_threadpool(grok_console.cleanup_preview, provider, statuses)
            if action == "cleanup":
                return await run_in_threadpool(grok_console.cleanup, provider, statuses)
            if action == "convert":
                target = str(body.get("target") or "build")
                strategy = str(body.get("strategy") or "missing")
                if not ids:
                    raise HTTPException(status_code=400, detail={"error": "ids 为空"})
                stream = await run_in_threadpool(grok_console.convert_accounts, target, ids, strategy)
                return {"stream": stream}
            if action == "convert_to_build":
                if not ids:
                    raise HTTPException(status_code=400, detail={"error": "ids 为空"})
                stream = await run_in_threadpool(grok_console.convert_to_build, provider, ids)
                return {"stream": stream}
            if action == "set_concurrency":
                value = int(body.get("value") or 8)
                return await run_in_threadpool(grok_console.set_concurrency, ids, value)
            if action == "detect":
                stream = await run_in_threadpool(grok_console.detect, provider, ids or None)
                return {"stream": stream}
        except GrokConsoleError as exc:
            raise _guard(exc) from exc
        raise HTTPException(status_code=400, detail={"error": f"未知操作 {action}"})

    @router.post("/api/grok/accounts/import")
    async def import_accounts(file: UploadFile = File(...), provider: str = Form(default=""),
                              authorization: str | None = Header(default=None)):
        require_admin(authorization)
        content = await file.read()
        try:
            stream = await run_in_threadpool(grok_console.import_accounts, file.filename or "accounts.txt", content, provider)
        except GrokConsoleError as exc:
            raise _guard(exc) from exc
        return {"stream": stream}

    @router.get("/api/grok/accounts/export")
    async def export_accounts(provider: str = "grok_web", limit: int = 500, format: str = "grok2api",
                              authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            if format == "grok2api":
                content = await run_in_threadpool(grok_console.export_accounts, provider, limit)
                return {"format": "grok2api", "content": content}
            return await run_in_threadpool(grok_console.export_formatted, provider, format)
        except GrokConsoleError as exc:
            raise _guard(exc) from exc

    @router.get("/api/grok/keys")
    async def keys(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return {"items": await run_in_threadpool(grok_console.keys)}
        except GrokConsoleError as exc:
            raise _guard(exc) from exc

    @router.post("/api/grok/keys")
    async def create_key(body: GrokKeyPayload, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        if not body.name.strip():
            raise HTTPException(status_code=400, detail={"error": "name 必填"})
        payload: dict[str, Any] = {"name": body.name.strip()}
        if body.rpm_limit is not None:
            payload["rpmLimit"] = body.rpm_limit
        if body.max_concurrent is not None:
            payload["maxConcurrent"] = body.max_concurrent
        if body.billing_limit_usd is not None:
            payload["billingLimitUsdTicks"] = int(body.billing_limit_usd * 1e9)
        if body.allowed_models:
            payload["allowedModelIds"] = body.allowed_models
        try:
            return await run_in_threadpool(grok_console.create_key, payload)
        except GrokConsoleError as exc:
            raise _guard(exc) from exc

    @router.patch("/api/grok/keys/{key_id}")
    async def patch_key(key_id: str, body: dict, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(grok_console.patch_key, key_id, body)
        except GrokConsoleError as exc:
            raise _guard(exc) from exc

    @router.get("/api/grok/keys/{key_id}/secret")
    async def reveal_key(key_id: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return {"secret": await run_in_threadpool(grok_console.reveal_key, key_id)}
        except GrokConsoleError as exc:
            raise _guard(exc) from exc

    @router.delete("/api/grok/keys/{key_id}")
    async def delete_key(key_id: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            await run_in_threadpool(grok_console.delete_key, key_id)
        except GrokConsoleError as exc:
            raise _guard(exc) from exc
        return {"ok": True}

    @router.get("/api/grok/audits")
    async def audits(page: int = 1, page_size: int = 20, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(grok_console.audits, page, page_size)
        except GrokConsoleError as exc:
            raise _guard(exc) from exc

    @router.get("/api/grok/egress")
    async def egress(page: int = 1, page_size: int = 20, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(grok_console.egress_nodes, page, page_size)
        except GrokConsoleError as exc:
            raise _guard(exc) from exc

    @router.get("/api/grok/media")
    async def media(page: int = 1, page_size: int = 20, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(grok_console.media, page, page_size)
        except GrokConsoleError as exc:
            raise _guard(exc) from exc

    @router.get("/api/grok/dashboard")
    async def dashboard(range: str = "24h", authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(grok_console.dashboard, range)
        except GrokConsoleError as exc:
            raise _guard(exc) from exc

    @router.post("/api/grok/accounts/{account_id}/egress")
    async def bind_egress(account_id: str, body: dict, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        node_id = str(body.get("egress_node_id") or "")
        mode = str(body.get("mode") or "manual")
        payload = {"egressNodeId": node_id or None, "egressAssignmentMode": mode}
        try:
            await run_in_threadpool(grok_console.patch_account, account_id, payload)
        except GrokConsoleError as exc:
            raise _guard(exc) from exc
        return {"ok": True}

    @router.post("/api/grok/accounts/{account_id}/script")
    async def account_script(account_id: str, body: dict, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        action = str(body.get("action") or "")
        try:
            await run_in_threadpool(grok_console.web_script, account_id, action)
        except GrokConsoleError as exc:
            raise _guard(exc) from exc
        return {"ok": True}

    @router.post("/api/grok/egress")
    async def egress_create(body: dict, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(grok_console.egress_create, body)
        except GrokConsoleError as exc:
            raise _guard(exc) from exc

    @router.put("/api/grok/egress/{node_id}")
    async def egress_update(node_id: str, body: dict, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(grok_console.egress_update, node_id, body)
        except GrokConsoleError as exc:
            raise _guard(exc) from exc

    @router.delete("/api/grok/egress/{node_id}")
    async def egress_delete(node_id: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            await run_in_threadpool(grok_console.egress_delete, node_id)
        except GrokConsoleError as exc:
            raise _guard(exc) from exc
        return {"ok": True}

    @router.post("/api/grok/egress/test")
    async def egress_test(body: dict, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(grok_console.egress_test, [str(i) for i in (body.get("ids") or [])])
        except GrokConsoleError as exc:
            raise _guard(exc) from exc

    @router.post("/api/grok/{kind}/generate")
    async def media_generate(kind: str, body: dict, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        if kind not in ("image", "video"):
            raise HTTPException(status_code=400, detail={"error": "不支持的类型"})
        try:
            return await run_in_threadpool(grok_console.media_generate, kind, body)
        except GrokConsoleError as exc:
            raise _guard(exc) from exc

    @router.post("/api/grok/speech")
    async def speech(body: dict, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            audio = await run_in_threadpool(grok_console.speech, body)
        except GrokConsoleError as exc:
            raise _guard(exc) from exc
        return Response(content=audio, media_type="audio/mpeg")

    @router.post("/api/grok/chat")
    async def grok_chat(body: dict, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(grok_console.chat, body)
        except GrokConsoleError as exc:
            raise _guard(exc) from exc

    @router.get("/api/grok/videos")
    async def videos(page: int = 1, page_size: int = 24, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(grok_console.videos, page, page_size)
        except GrokConsoleError as exc:
            raise _guard(exc) from exc

    @router.get("/api/grok/guard")
    async def guard(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(grok_console.guard_status)
        except GrokConsoleError as exc:
            raise _guard(exc) from exc

    @router.get("/api/grok/model-routes")
    async def model_routes(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return {"items": await run_in_threadpool(grok_console.model_routes)}
        except GrokConsoleError as exc:
            raise _guard(exc) from exc

    @router.patch("/api/grok/model-routes/{route_id}")
    async def update_model_route(route_id: str, body: dict, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(grok_console.update_model_route, route_id, body)
        except GrokConsoleError as exc:
            raise _guard(exc) from exc

    @router.get("/api/grok/videos/{video_id}")
    async def video_status(video_id: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return await run_in_threadpool(grok_console.video_status, video_id)
        except GrokConsoleError as exc:
            raise _guard(exc) from exc

    @router.get("/api/grok/models")
    async def models(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            return {"items": await run_in_threadpool(grok_console.models)}
        except GrokConsoleError as exc:
            raise _guard(exc) from exc

    return router

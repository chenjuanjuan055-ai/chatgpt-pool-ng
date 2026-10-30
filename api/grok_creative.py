"""创作控制台 API：把 grok2api 的 /v1 公开接口透传给前端。

与 grok2api 自带的 Creative Console 用同一套端点：
  POST /v1/responses           流式对话（web_search / x_search / reasoning）
  POST /v1/images/generations  图片生成
  POST /v1/videos              视频生成
  POST /v1/videos/edits        视频编辑
  POST /v1/videos/extensions   视频续写
  GET  /v1/videos/{id}         视频状态轮询
  POST /v1/tts                 语音合成
  POST /v1/stt                 语音转写
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Body, File, Header, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from api.support import require_admin
from services.grok_console import GrokConsoleError, grok_console

PUBLIC_PREFIX = "/v1/"


def _guard(error: Exception) -> HTTPException:
    return HTTPException(status_code=502, detail={"error": str(error)[:300]})


def create_router() -> APIRouter:
    router = APIRouter()

    @router.post("/api/grok/v1/responses")
    async def responses(request: Request, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        payload = await request.json()
        try:
            upstream = grok_console.public_stream("/v1/responses", payload)
        except GrokConsoleError as exc:
            raise _guard(exc) from exc

        def iterator():
            try:
                while True:
                    chunk = upstream.read(4096)
                    if not chunk:
                        break
                    yield chunk
            finally:
                upstream.close()

        return StreamingResponse(
            iterator(),
            media_type=upstream.headers.get("content-type", "text/event-stream"),
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.post("/api/grok/media/upload")
    async def upload_media(file: UploadFile = File(...), authorization: str | None = Header(default=None)):
        require_admin(authorization)
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail={"error": "文件为空"})
        try:
            data = await run_in_threadpool(
                grok_console.upload_media,
                file.filename or "upload.bin",
                content,
                file.content_type or "application/octet-stream",
            )
        except GrokConsoleError as exc:
            raise _guard(exc) from exc
        return data

    @router.post("/api/grok/media/import")
    async def import_media(payload: dict = Body(...), authorization: str | None = Header(default=None)):
        require_admin(authorization)
        url = str(payload.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail={"error": "需要 http(s) 地址"})
        try:
            data = await run_in_threadpool(
                grok_console._auth_call, "/api/admin/v1/media/inputs/import",
                "POST", {"url": url},
            )
        except GrokConsoleError as exc:
            raise _guard(exc) from exc
        return (data or {}).get("data") or {}

    @router.get("/api/grok/v1/media/{path:path}")
    async def media(path: str, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        try:
            upstream = grok_console.public_open("/v1/media/" + path)
        except GrokConsoleError as exc:
            raise _guard(exc) from exc

        def iterator():
            try:
                while True:
                    chunk = upstream.read(65536)
                    if not chunk:
                        break
                    yield chunk
            finally:
                upstream.close()

        return StreamingResponse(
            iterator(),
            media_type=upstream.headers.get("content-type", "application/octet-stream"),
            headers={"Cache-Control": "private, max-age=3600"},
        )

    @router.api_route("/api/grok/v1/{path:path}", methods=["GET", "POST", "DELETE"])
    async def passthrough(path: str, request: Request, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        payload: dict[str, Any] | None = None
        if request.method in ("POST", "PUT", "PATCH"):
            raw = await request.body()
            if raw:
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except Exception as exc:
                    raise HTTPException(status_code=400, detail={"error": "请求体不是合法 JSON"}) from exc
        try:
            data = grok_console.public_call(PUBLIC_PREFIX + path, method=request.method, payload=payload)
        except GrokConsoleError as exc:
            raise _guard(exc) from exc
        return data

    return router
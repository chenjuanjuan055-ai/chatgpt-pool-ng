"""Model catalog: unify chat/image model lists, defaults and capabilities.

Pure builder (`build_catalog`) keeps the data shaping testable; the wrapper
merges the live upstream catalog with account information and config overrides.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Iterable

from services.account_service import account_service
from services.config import config
from services.model_service import model_catalog_service as upstream_catalog_service
from utils.helper import CODEX_IMAGE_MODEL, IMAGE_MODEL_PLAN_TYPES, WEB_IMAGE_MODELS

FALLBACK_CHAT_MODELS = ["gpt-5.6", "auto"]


def _unique(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _codex_types(accounts: Iterable[dict[str, Any]]) -> set[str]:
    types: set[str] = set()
    for account in accounts:
        if not isinstance(account, dict):
            continue
        source = str(account.get("source_type") or "").strip().lower()
        export_type = str(account.get("export_type") or "").strip().lower()
        if source != "codex" and export_type != "codex":
            continue
        plan = str(account.get("type") or "").strip()
        if plan and plan.lower() not in {"free", "unknown"}:
            types.add(plan)
    return types


def build_catalog(
    *,
    settings: dict[str, Any] | None = None,
    upstream_models: Iterable[str] | None = None,
    accounts: Iterable[dict[str, Any]] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    settings = settings if isinstance(settings, dict) else {}
    accounts = list(accounts or [])
    configured_chat = _unique(settings.get("chat_models") or [])
    configured_image = _unique(settings.get("image_models") or [])
    upstream = _unique(upstream_models or [])

    chat_source = "config" if configured_chat else ("upstream" if upstream else "fallback")
    chat_models = configured_chat or upstream or list(FALLBACK_CHAT_MODELS)

    codex_types = _codex_types(accounts)
    codex_models = [CODEX_IMAGE_MODEL] + [f"{plan.lower()}-{CODEX_IMAGE_MODEL}" for plan in sorted(codex_types)]
    image_models = configured_image or _unique([*WEB_IMAGE_MODELS, *codex_models])
    image_source = "config" if configured_image else "fallback"

    defaults = {
        "chat_model": "auto" if "auto" in chat_models else chat_models[0],
        "image_model": "gpt-image-2" if "gpt-image-2" in image_models else image_models[0],
    }
    high_resolution = [model for model in image_models if model == CODEX_IMAGE_MODEL or model.endswith(f"-{CODEX_IMAGE_MODEL}")]
    payload = {
        "chat_models": chat_models,
        "image_models": image_models,
        "defaults": defaults,
        "capabilities": {
            "image_upscale": bool(settings.get("image_upscale_enabled")),
            "high_resolution_image_models": high_resolution,
            "image_upstream_model": str(settings.get("image_upstream_model") or "gpt-5-3"),
        },
        "source": {"chat": chat_source, "image": image_source},
    }
    revision = hashlib.sha1(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
    return {
        "generated_at": time.time() if now is None else now,
        "revision": revision,
        **payload,
        "all_models": _unique([*chat_models, *image_models]),
    }


def get_model_catalog() -> dict[str, Any]:
    settings = config.get_model_catalog_settings()
    try:
        upstream = [str(item.get("id") or "") for item in upstream_catalog_service.list_models().get("data") or []]
    except Exception:
        upstream = []
    return build_catalog(settings=settings, upstream_models=upstream, accounts=account_service.list_accounts())


__all__ = ["FALLBACK_CHAT_MODELS", "build_catalog", "get_model_catalog"]

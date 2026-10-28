"""Prompt library stored in config.json (create/list/delete)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from services.config import config


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_item(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    title = str(value.get("title") or "").strip()
    content = str(value.get("content") or "").strip()
    if not title or not content:
        return None
    tags_value = value.get("tags")
    tags: list[str] = []
    if isinstance(tags_value, list):
        for tag in tags_value:
            text = str(tag or "").strip()
            if text and text not in tags:
                tags.append(text)
    return {
        "id": str(value.get("id") or uuid.uuid4().hex[:12]),
        "title": title,
        "content": content,
        "tags": tags,
        "created_at": value.get("created_at") or _now_iso(),
    }


def normalize_items(value: object) -> list[dict[str, Any]]:
    items = value if isinstance(value, list) else []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        normalized = normalize_item(item)
        if normalized and normalized["id"] not in seen:
            seen.add(normalized["id"])
            result.append(normalized)
    return result


def list_prompts() -> list[dict[str, Any]]:
    return config.get_prompt_settings()


def create_prompt(title: str, content: str, tags: list[str] | None = None) -> dict[str, Any]:
    item = normalize_item({"title": title, "content": content, "tags": tags or []})
    if item is None:
        raise ValueError("标题和内容不能为空")
    items = list_prompts()
    items.append(item)
    config.update({"prompts": items})
    return item


def delete_prompt(prompt_id: str) -> bool:
    target = str(prompt_id or "").strip()
    items = list_prompts()
    remaining = [item for item in items if item["id"] != target]
    if len(remaining) == len(items):
        return False
    config.update({"prompts": remaining})
    return True

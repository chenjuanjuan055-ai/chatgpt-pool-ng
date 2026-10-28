"""Account groups: CRUD on config plus a pure membership filter.

Groups let keys see only their own slice of the pool. Storage lives in
``config.json -> account_groups`` so it survives restarts without a schema
migration on the JSON/SQL account stores.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from services.config import config


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_group(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    group_id = str(value.get("id") or "").strip()
    name = str(value.get("name") or "").strip()
    if not group_id or not name:
        return None
    return {
        "id": group_id,
        "name": name,
        "description": str(value.get("description") or "").strip(),
        "enabled": bool(value.get("enabled", True)),
        "created_at": value.get("created_at") or _now_iso(),
    }


def normalize_groups(value: object) -> list[dict[str, Any]]:
    items = value if isinstance(value, list) else []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        group = normalize_group(item)
        if group and group["id"] not in seen:
            seen.add(group["id"])
            result.append(group)
    return result


def list_groups() -> list[dict[str, Any]]:
    return normalize_groups(config.get_account_groups_settings())


def get_group(group_id: str) -> dict[str, Any] | None:
    target = str(group_id or "").strip()
    return next((group for group in list_groups() if group["id"] == target), None)


def create_group(name: str, description: str = "") -> dict[str, Any]:
    clean_name = str(name or "").strip()
    if not clean_name:
        raise ValueError("分组名称不能为空")
    groups = list_groups()
    if any(group["name"] == clean_name for group in groups):
        raise ValueError("分组名称已存在")
    group = {
        "id": uuid.uuid4().hex[:12],
        "name": clean_name,
        "description": str(description or "").strip(),
        "enabled": True,
        "created_at": _now_iso(),
    }
    groups.append(group)
    config.update({"account_groups": groups})
    return group


def update_group(group_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    target = str(group_id or "").strip()
    groups = list_groups()
    for index, group in enumerate(groups):
        if group["id"] != target:
            continue
        if updates.get("name") is not None:
            name = str(updates.get("name") or "").strip()
            if not name:
                raise ValueError("分组名称不能为空")
            if any(item["name"] == name and item["id"] != target for item in groups):
                raise ValueError("分组名称已存在")
            group["name"] = name
        if updates.get("description") is not None:
            group["description"] = str(updates.get("description") or "").strip()
        if updates.get("enabled") is not None:
            group["enabled"] = bool(updates.get("enabled"))
        groups[index] = group
        config.update({"account_groups": groups})
        return group
    return None


def delete_group(group_id: str) -> bool:
    target = str(group_id or "").strip()
    groups = list_groups()
    remaining = [group for group in groups if group["id"] != target]
    if len(remaining) == len(groups):
        return False
    config.update({"account_groups": remaining})
    return True


def account_in_group(account: dict[str, Any], group_id: str) -> bool:
    target = str(group_id or "").strip()
    if not target:
        return True
    return str(account.get("group_id") or "").strip() == target


def filter_accounts(accounts: Iterable[dict[str, Any]], group_id: str) -> list[dict[str, Any]]:
    return [account for account in accounts if account_in_group(account, group_id)]

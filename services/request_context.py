"""Per-request context (group scoping for account selection)."""

from __future__ import annotations

from contextvars import ContextVar

current_group_id: ContextVar[str] = ContextVar("current_group_id", default="")


def set_request_group(group_id: object) -> None:
    current_group_id.set(str(group_id or "").strip())


def get_request_group() -> str:
    return current_group_id.get()

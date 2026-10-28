"""Account health classification and pool summaries.

Pure functions so they can be unit tested without storage or network.
The classifier powers the health summary / dead-account cleanup APIs and
the import-time credential checks.
"""

from __future__ import annotations

import base64
import json
import time
from datetime import datetime, timezone
from typing import Any, Iterable

# Classification codes exposed through the API / UI.
STATUS_OK = "ok"
STATUS_EXPIRING = "expiring"
STATUS_AT_ONLY = "at_only"
STATUS_AT_EXPIRED_NO_RT = "at_expired_no_rt"
STATUS_REFRESH_INVALID = "refresh_invalid"
STATUS_MISSING_AT = "missing_at"
STATUS_CODEX_MANAGED = "codex_managed"

DEAD_STATUSES = frozenset({STATUS_AT_EXPIRED_NO_RT, STATUS_REFRESH_INVALID, STATUS_MISSING_AT})
AT_ONLY_STATUSES = frozenset({STATUS_AT_ONLY})

STATUS_LABELS = {
    STATUS_OK: "正常",
    STATUS_EXPIRING: "即将过期",
    STATUS_AT_ONLY: "仅 AT（不可续期）",
    STATUS_AT_EXPIRED_NO_RT: "AT 过期且无 RT",
    STATUS_REFRESH_INVALID: "RT 已失效",
    STATUS_MISSING_AT: "缺少 AT",
    STATUS_CODEX_MANAGED: "Codex 托管",
}

_REFRESH_INVALID_MARKERS = (
    "invalidated",
    "session has ended",
    "token_expired",
    "invalid_grant",
    "401",
    "revoked",
)

EXPIRING_WINDOW_SECONDS = 24 * 3600


def _decode_jwt_exp(token: str) -> int | None:
    token = str(token or "").strip()
    parts = token.split(".")
    if len(parts) < 2:
        return None
    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    try:
        raw = base64.urlsafe_b64decode((payload + padding).encode("ascii"))
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return None
    exp = data.get("exp")
    try:
        return int(exp) if exp is not None else None
    except (TypeError, ValueError):
        return None


def _parse_timestamp(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _has_refresh_token(account: dict[str, Any]) -> bool:
    for key in ("refresh_token", "refreshToken"):
        if str(account.get(key) or "").strip():
            return True
    return False


def _is_codex_managed(account: dict[str, Any]) -> bool:
    source = str(account.get("source_type") or "").strip().lower()
    export_type = str(account.get("export_type") or "").strip().lower()
    return source == "codex" or export_type == "codex"


def _refresh_error(account: dict[str, Any]) -> str:
    for key in ("last_token_refresh_error", "last_refresh_error"):
        value = str(account.get(key) or "").strip()
        if value:
            return value
    return ""


def classify_account(account: dict[str, Any], now: float | None = None) -> dict[str, Any]:
    """Return a health record for one account."""
    now = time.time() if now is None else now
    access_token = str(account.get("access_token") or "").strip()
    has_rt = _has_refresh_token(account)
    codex_managed = _is_codex_managed(account)
    exp = _decode_jwt_exp(access_token) if access_token else None
    expires_in = (exp - now) if exp is not None else None

    if not access_token:
        status = STATUS_MISSING_AT
    elif codex_managed and not has_rt:
        status = STATUS_CODEX_MANAGED
    elif codex_managed:
        status = STATUS_CODEX_MANAGED
    elif not has_rt:
        if exp is not None and exp <= now:
            status = STATUS_AT_EXPIRED_NO_RT
        else:
            status = STATUS_AT_ONLY
    else:
        error = _refresh_error(account)
        lowered = error.lower()
        if error and any(marker in lowered for marker in _REFRESH_INVALID_MARKERS):
            status = STATUS_REFRESH_INVALID
        elif exp is not None and exp <= now:
            # RT present: the refresher can still recover this account.
            status = STATUS_EXPIRING
        elif expires_in is not None and expires_in <= EXPIRING_WINDOW_SECONDS:
            status = STATUS_EXPIRING
        else:
            status = STATUS_OK

    dead = status in DEAD_STATUSES
    return {
        "status": status,
        "label": STATUS_LABELS.get(status, status),
        "dead": dead,
        "at_only": status in AT_ONLY_STATUSES,
        "has_refresh_token": has_rt,
        "access_token_expires_at": exp,
        "access_token_expires_in": expires_in,
        "refresh_error": _refresh_error(account),
    }


def _age_bucket(created_at: Any, now: float) -> str:
    created = _parse_timestamp(created_at)
    if created is None:
        return "unknown"
    age_days = max(0.0, (now - created) / 86400)
    if age_days < 7:
        return "0-7d"
    if age_days < 30:
        return "7-30d"
    if age_days < 90:
        return "30-90d"
    return "90d+"


def _mask_token(token: str) -> str:
    token = str(token or "")
    if len(token) <= 12:
        return token[:4] + "…" if token else ""
    return f"{token[:6]}…{token[-4:]}"


def analyze_accounts(
    accounts: Iterable[dict[str, Any]],
    now: float | None = None,
    *,
    sample_limit: int = 200,
) -> dict[str, Any]:
    """Aggregate a pool into counts, age buckets and dead account samples."""
    now = time.time() if now is None else now
    counts: dict[str, int] = {}
    age_counts: dict[str, dict[str, int]] = {}
    type_counts: dict[str, int] = {}
    dead_samples: list[dict[str, Any]] = []
    at_only_samples: list[dict[str, Any]] = []
    total = 0

    for account in accounts:
        if not isinstance(account, dict):
            continue
        total += 1
        record = classify_account(account, now=now)
        status = record["status"]
        counts[status] = counts.get(status, 0) + 1
        bucket = _age_bucket(account.get("created_at"), now)
        bucket_map = age_counts.setdefault(bucket, {})
        bucket_map[status] = bucket_map.get(status, 0) + 1
        plan = str(account.get("type") or "unknown").strip().lower() or "unknown"
        type_counts[plan] = type_counts.get(plan, 0) + 1

        sample = {
            "access_token": _mask_token(account.get("access_token")),
            "email": account.get("email") or "",
            "type": plan,
            "created_at": account.get("created_at"),
            "last_used_at": account.get("last_used_at"),
            "status": status,
            "label": record["label"],
            "refresh_error": record["refresh_error"][:200],
            "access_token_expires_in": record["access_token_expires_in"],
        }
        if record["dead"] and len(dead_samples) < sample_limit:
            dead_samples.append(sample)
        elif record["at_only"] and len(at_only_samples) < sample_limit:
            at_only_samples.append(sample)

    dead_total = sum(counts.get(status, 0) for status in DEAD_STATUSES)
    return {
        "total": total,
        "healthy": counts.get(STATUS_OK, 0) + counts.get(STATUS_CODEX_MANAGED, 0),
        "dead": dead_total,
        "at_only": sum(counts.get(status, 0) for status in AT_ONLY_STATUSES),
        "status_counts": counts,
        "status_labels": STATUS_LABELS,
        "age_buckets": age_counts,
        "type_counts": type_counts,
        "dead_samples": dead_samples,
        "at_only_samples": at_only_samples,
        "generated_at": now,
    }


def validate_import_payload(
    account: dict[str, Any],
    *,
    now: float | None = None,
    require_refresh_token: bool = True,
) -> tuple[bool, str]:
    """Check one account payload before import.

    Returns (accept, reason). Codex-managed accounts are exempt because
    their credentials come from the CPA pool. Everything else must carry a
    refresh token, otherwise it silently dies once the AT expires.
    """
    if not isinstance(account, dict):
        return False, "账号条目格式无效"
    access_token = str(account.get("access_token") or account.get("accessToken") or "").strip()
    if not access_token:
        return False, "缺少 access_token"
    if not require_refresh_token:
        return True, ""
    if _is_codex_managed(account):
        return True, ""
    if not _has_refresh_token(account):
        return False, "缺少 refresh_token：AT 过期后无法续期（仅 AT 的号会在数天内全部失效）"
    exp = _decode_jwt_exp(access_token)
    now_ts = time.time() if now is None else now
    if exp is not None and exp <= now_ts:
        return False, "access_token 已过期：请先刷新或重新登录后再导入"
    return True, ""


def filter_import_payloads(
    accounts: Iterable[dict[str, Any]],
    *,
    now: float | None = None,
    require_refresh_token: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for account in accounts:
        if not isinstance(account, dict):
            continue
        ok, reason = validate_import_payload(account, now=now, require_refresh_token=require_refresh_token)
        if ok:
            accepted.append(account)
        else:
            rejected.append(
                {
                    "email": account.get("email") or "",
                    "access_token": _mask_token(account.get("access_token") or account.get("accessToken") or ""),
                    "reason": reason,
                }
            )
    return accepted, rejected

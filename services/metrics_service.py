"""In-process request metrics and alert evaluation.

Records every /v1 call reported by ``LoggedCall.log`` into a bounded ring
buffer and exposes aggregated snapshots. Alerts are evaluated on write and
dispatched to a webhook (generic JSON) and/or AstrBot QQ bridge.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from collections import Counter, deque
from typing import Any, Deque, Iterable

from services import error_taxonomy
from services.config import DATA_DIR, config
from services.hourly_metrics import HourlyMetrics

MAX_RECORDS = 4000

hourly_metrics = HourlyMetrics(DATA_DIR / "metrics_hourly.json")


def _percentile(values: list[float], ratio: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * ratio))))
    return round(ordered[index], 1)


class MetricsService:
    def __init__(self, max_records: int = MAX_RECORDS) -> None:
        self._records: Deque[dict[str, Any]] = deque(maxlen=max_records)
        self._lock = threading.RLock()
        self._alert_lock = threading.Lock()
        self._last_alert_at = 0.0
        self._consecutive_failures = 0

    # -- recording -------------------------------------------------------
    def record_call(
        self,
        *,
        endpoint: str,
        model: str,
        status: str,
        duration_ms: int | float | None = None,
        error: str = "",
        account_email: str = "",
        now: float | None = None,
    ) -> dict[str, Any]:
        now = time.time() if now is None else now
        failed = str(status).lower() != "success"
        category = error_taxonomy.classify(error, status=status) if failed else ""
        record = {
            "ts": now,
            "endpoint": str(endpoint or ""),
            "model": str(model or ""),
            "status": "failed" if failed else "success",
            "duration_ms": round(float(duration_ms), 1) if duration_ms is not None else None,
            "error": str(error or "")[:500],
            "error_category": category,
            "account_email": str(account_email or ""),
        }
        with self._lock:
            self._records.append(record)
            if failed:
                self._consecutive_failures += 1
            else:
                self._consecutive_failures = 0
            consecutive = self._consecutive_failures
        hourly_metrics.add(
            ts=now,
            status=record["status"],
            model=record["model"],
            error_category=record["error_category"],
            latency_ms=record["duration_ms"],
        )
        self._maybe_alert(record, consecutive, now)
        return record

    # -- aggregation -----------------------------------------------------
    def snapshot(self, *, window_seconds: int = 3600, now: float | None = None, sample_limit: int = 20) -> dict[str, Any]:
        now = time.time() if now is None else now
        cutoff = now - max(1, int(window_seconds))
        with self._lock:
            records = [item for item in self._records if item["ts"] >= cutoff]

        total = len(records)
        failed = sum(1 for item in records if item["status"] == "failed")
        durations = [item["duration_ms"] for item in records if item["duration_ms"] is not None]
        by_model: dict[str, dict[str, Any]] = {}
        by_category: Counter[str] = Counter()
        by_endpoint: Counter[str] = Counter()
        for item in records:
            model = item["model"] or "(unknown)"
            bucket = by_model.setdefault(model, {"total": 0, "failed": 0, "durations": []})
            bucket["total"] += 1
            if item["status"] == "failed":
                bucket["failed"] += 1
                if item["error_category"]:
                    by_category[item["error_category"]] += 1
            if item["duration_ms"] is not None:
                bucket["durations"].append(item["duration_ms"])
            by_endpoint[item["endpoint"]] += 1

        model_rows = []
        for model, bucket in sorted(by_model.items(), key=lambda pair: pair[1]["total"], reverse=True):
            model_rows.append(
                {
                    "model": model,
                    "total": bucket["total"],
                    "failed": bucket["failed"],
                    "success_rate": round((bucket["total"] - bucket["failed"]) / bucket["total"], 4)
                    if bucket["total"]
                    else None,
                    "p95_ms": _percentile(bucket["durations"], 0.95),
                }
            )

        recent_failures = [
            {
                "ts": item["ts"],
                "model": item["model"],
                "endpoint": item["endpoint"],
                "error_category": item["error_category"],
                "error": item["error"][:200],
                "account_email": item["account_email"],
            }
            for item in reversed(records)
            if item["status"] == "failed"
        ][:sample_limit]

        minute_rpm = sum(1 for item in records if item["ts"] >= now - 60)
        return {
            "window_seconds": window_seconds,
            "generated_at": now,
            "total": total,
            "failed": failed,
            "success": total - failed,
            "success_rate": round((total - failed) / total, 4) if total else None,
            "rpm": minute_rpm,
            "p50_ms": _percentile(durations, 0.5),
            "p95_ms": _percentile(durations, 0.95),
            "by_model": model_rows,
            "by_error_category": [
                {"category": key, **error_taxonomy.describe(key), "count": count}
                for key, count in by_category.most_common()
            ],
            "by_endpoint": [{"endpoint": key, "count": count} for key, count in by_endpoint.most_common()],
            "recent_failures": recent_failures,
        }

    def dashboard(self, hours: int = 24, now: float | None = None) -> dict[str, Any]:
        hours = max(1, min(int(hours or 24), 24 * 14))
        return {
            "totals": hourly_metrics.totals(hours=hours, now=now),
            "rows": hourly_metrics.rows(hours=hours, now=now),
        }

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._records)[-max(1, min(limit, MAX_RECORDS)):][::-1]

    # -- alerts ----------------------------------------------------------
    def _maybe_alert(self, record: dict[str, Any], consecutive_failures: int, now: float) -> None:
        try:
            settings = config.get_alert_settings()
        except Exception:
            return
        if not _has_alert_channel(settings):
            return
        snapshot = None
        if record["status"] == "failed":
            window = int(settings.get("window_seconds") or 300)
            snapshot = self.snapshot(window_seconds=window, now=now, sample_limit=1)
        if not evaluate_alert(settings, snapshot, consecutive_failures):
            return
        cooldown = float(settings.get("cooldown_sec") or 300)
        with self._alert_lock:
            if now - self._last_alert_at < cooldown:
                return
            self._last_alert_at = now
        message = {
            "event": "pool_alert",
            "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
            "consecutive_failures": consecutive_failures,
            "last_error": record["error"][:300],
            "error_category": error_taxonomy.describe(record["error_category"]),
            "model": record["model"],
            "endpoint": record["endpoint"],
        }
        threading.Thread(target=self._dispatch, args=(settings, message), name="alert-dispatch", daemon=True).start()

    def _dispatch(self, settings: dict[str, Any], message: dict[str, Any]) -> None:
        text = f"[号池告警] {message['error_category'].get('label') or '异常'} | 连续失败 {message['consecutive_failures']} 次 | {message['model']}\n{message['last_error']}"
        webhook = str(settings.get("webhook_url") or "").strip()
        if webhook:
            self._post_json(webhook, message, {})
        qq = settings.get("qq") if isinstance(settings.get("qq"), dict) else {}
        qq_url = str(qq.get("url") or "").strip()
        if qq_url:
            headers = {}
            token = str(qq.get("token") or "").strip()
            if token:
                headers["Authorization"] = f"Bearer {token}"
            payload = {"umo": str(qq.get("umo") or ""), "message": text}
            self._post_json(qq_url, payload, headers)

    def test_dispatch(self) -> dict[str, Any]:
        settings = config.get_alert_settings()
        message = {
            "event": "pool_alert_test",
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "consecutive_failures": 0,
            "last_error": "这是一条测试告警",
            "error_category": error_taxonomy.describe(error_taxonomy.UNKNOWN),
            "model": "-",
            "endpoint": "/api/alerts/test",
        }
        text = "[号池告警] 测试消息：告警通道工作正常"
        result = {"webhook": None, "qq": None}
        webhook = str(settings.get("webhook_url") or "").strip()
        if webhook:
            result["webhook"] = self._post_json(webhook, message, {})
        qq = settings.get("qq") if isinstance(settings.get("qq"), dict) else {}
        qq_url = str(qq.get("url") or "").strip()
        if qq_url:
            headers = {}
            token = str(qq.get("token") or "").strip()
            if token:
                headers["Authorization"] = f"Bearer {token}"
            result["qq"] = self._post_json(
                qq_url, {"umo": str(qq.get("umo") or ""), "message": text}, headers
            )
        return result

    @staticmethod
    def _post_json(url: str, payload: dict[str, Any], headers: dict[str, str]) -> bool:
        try:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            request = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json", **headers},
                method="POST",
            )
            urllib.request.urlopen(request, timeout=8).read()
            return True
        except Exception:
            return False


def _has_alert_channel(settings: dict[str, Any]) -> bool:
    webhook = str(settings.get("webhook_url") or "").strip()
    qq = settings.get("qq") if isinstance(settings.get("qq"), dict) else {}
    return bool(webhook or str(qq.get("url") or "").strip())


def evaluate_alert(
    settings: dict[str, Any],
    snapshot: dict[str, Any] | None,
    consecutive_failures: int,
) -> bool:
    """Decide whether an alert should fire. Pure function for tests."""
    if not settings.get("enabled") or not _has_alert_channel(settings):
        return False
    threshold = int(settings.get("consecutive_failures") or 0)
    if threshold and consecutive_failures >= threshold:
        return True
    if not snapshot:
        return False
    min_requests = int(settings.get("min_requests") or 20)
    max_rate = float(settings.get("max_failure_rate") or 0.5)
    total = int(snapshot.get("total") or 0)
    if total < min_requests:
        return False
    success_rate = snapshot.get("success_rate")
    if success_rate is None:
        return False
    return success_rate <= (1 - max_rate)


metrics_service = MetricsService()

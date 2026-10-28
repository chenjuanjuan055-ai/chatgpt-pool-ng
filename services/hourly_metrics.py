"""Hourly request aggregates persisted to disk for the dashboard.

Keeps a small rolling window (default 7 days) of per-hour counters so the
dashboard survives restarts without a database migration.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

HOUR_SECONDS = 3600


def hour_key(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H", time.gmtime(int(ts)))


class HourlyMetrics:
    def __init__(self, path: Path | None = None, retention_hours: int = 24 * 7) -> None:
        self.path = path
        self.retention_hours = max(1, int(retention_hours))
        self._lock = threading.RLock()
        self._buckets: dict[str, dict[str, Any]] = {}
        self._writes_since_flush = 0
        self._load()

    # -- persistence -----------------------------------------------------
    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return
        if isinstance(data, dict):
            self._buckets = {str(key): value for key, value in data.items() if isinstance(value, dict)}

    def flush(self) -> None:
        if self.path is None:
            return
        with self._lock:
            data = dict(self._buckets)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass

    # -- writes ----------------------------------------------------------
    def add(
        self,
        *,
        ts: float,
        status: str,
        model: str,
        error_category: str,
        latency_ms: float | None,
    ) -> None:
        failed = str(status).lower() != "success"
        key = hour_key(ts)
        with self._lock:
            bucket = self._buckets.setdefault(
                key,
                {"total": 0, "failed": 0, "lat_sum": 0.0, "lat_n": 0, "by_model": {}, "by_category": {}},
            )
            bucket["total"] += 1
            if failed:
                bucket["failed"] += 1
                if error_category:
                    bucket["by_category"][error_category] = bucket["by_category"].get(error_category, 0) + 1
            if latency_ms is not None:
                bucket["lat_sum"] += float(latency_ms)
                bucket["lat_n"] += 1
            model_key = str(model or "(unknown)")
            model_bucket = bucket["by_model"].setdefault(model_key, {"total": 0, "failed": 0})
            model_bucket["total"] += 1
            if failed:
                model_bucket["failed"] += 1
            self._prune_locked(now=ts)
            self._writes_since_flush += 1
            should_flush = self._writes_since_flush >= 50
            if should_flush:
                self._writes_since_flush = 0
        if should_flush:
            self.flush()

    def _prune_locked(self, now: float) -> None:
        cutoff = hour_key(now - self.retention_hours * HOUR_SECONDS)
        for key in [key for key in self._buckets if key < cutoff]:
            self._buckets.pop(key, None)

    # -- reads -----------------------------------------------------------
    def rows(self, hours: int = 24, now: float | None = None) -> list[dict[str, Any]]:
        now = time.time() if now is None else now
        cutoff = hour_key(now - max(1, int(hours)) * HOUR_SECONDS)
        with self._lock:
            keys = sorted(key for key in self._buckets if key >= cutoff)
            rows = []
            for key in keys:
                bucket = self._buckets[key]
                total = int(bucket.get("total") or 0)
                failed = int(bucket.get("failed") or 0)
                lat_n = int(bucket.get("lat_n") or 0)
                lat_sum = float(bucket.get("lat_sum") or 0.0)
                rows.append(
                    {
                        "hour": key,
                        "total": total,
                        "failed": failed,
                        "success_rate": round((total - failed) / total, 4) if total else None,
                        "avg_ms": round(lat_sum / lat_n, 1) if lat_n else None,
                        "by_model": dict(bucket.get("by_model") or {}),
                        "by_category": dict(bucket.get("by_category") or {}),
                    }
                )
            return rows

    def totals(self, hours: int = 24, now: float | None = None) -> dict[str, Any]:
        rows = self.rows(hours=hours, now=now)
        total = sum(row["total"] for row in rows)
        failed = sum(row["failed"] for row in rows)
        latency_weighted = sum((row["avg_ms"] or 0) * row["total"] for row in rows if row["avg_ms"] is not None)
        latency_requests = sum(row["total"] for row in rows if row["avg_ms"] is not None)
        by_category: dict[str, int] = {}
        by_model: dict[str, dict[str, int]] = {}
        for row in rows:
            for category, count in row["by_category"].items():
                by_category[category] = by_category.get(category, 0) + int(count)
            for model, bucket in row["by_model"].items():
                target = by_model.setdefault(model, {"total": 0, "failed": 0})
                target["total"] += int(bucket.get("total") or 0)
                target["failed"] += int(bucket.get("failed") or 0)
        return {
            "hours": max(1, int(hours)),
            "total": total,
            "failed": failed,
            "success_rate": round((total - failed) / total, 4) if total else None,
            "avg_ms": round(latency_weighted / latency_requests, 1) if latency_requests else None,
            "by_category": by_category,
            "by_model": by_model,
        }

"""Unit tests for persisted hourly aggregates."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from services.hourly_metrics import HourlyMetrics, hour_key

NOW = 1_800_000_000.0


class HourlyMetricsTest(unittest.TestCase):
    def test_rows_and_totals(self):
        metrics = HourlyMetrics(path=None)
        metrics.add(ts=NOW - 30, status="success", model="m1", error_category="", latency_ms=100)
        metrics.add(ts=NOW - 20, status="failed", model="m1", error_category="rate_limit", latency_ms=300)
        metrics.add(ts=NOW - 10, status="success", model="m2", error_category="", latency_ms=200)
        rows = metrics.rows(hours=2, now=NOW)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["total"], 3)
        self.assertEqual(row["failed"], 1)
        self.assertAlmostEqual(row["success_rate"], round(2 / 3, 4))
        self.assertEqual(row["avg_ms"], 200.0)
        totals = metrics.totals(hours=2, now=NOW)
        self.assertEqual(totals["total"], 3)
        self.assertEqual(totals["by_category"], {"rate_limit": 1})
        self.assertEqual(totals["by_model"]["m1"], {"total": 2, "failed": 1})

    def test_hour_buckets_split(self):
        metrics = HourlyMetrics(path=None)
        base = NOW - (NOW % 3600)
        metrics.add(ts=base - 10, status="success", model="m", error_category="", latency_ms=None)
        metrics.add(ts=base + 10, status="success", model="m", error_category="", latency_ms=None)
        rows = metrics.rows(hours=2, now=NOW + 10)
        self.assertEqual(len(rows), 2)
        self.assertEqual([row["total"] for row in rows], [1, 1])

    def test_prune_and_persist(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hourly.json"
            metrics = HourlyMetrics(path=path, retention_hours=2)
            metrics.add(ts=NOW - 3 * 3600, status="success", model="old", error_category="", latency_ms=1)
            metrics.add(ts=NOW, status="success", model="new", error_category="", latency_ms=1)
            metrics.flush()
            self.assertTrue(path.exists())

            reloaded = HourlyMetrics(path=path, retention_hours=2)
            rows = reloaded.rows(hours=2, now=NOW)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["by_model"], {"new": {"total": 1, "failed": 0}})

    def test_hour_key_is_utc_hour(self):
        self.assertEqual(hour_key(0), "1970-01-01T00")


if __name__ == "__main__":
    unittest.main()

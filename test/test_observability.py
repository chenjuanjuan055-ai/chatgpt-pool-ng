"""Unit tests for error taxonomy, metrics aggregation and alert decisions."""

from __future__ import annotations

import unittest

from services import error_taxonomy as tax
from services.metrics_service import MetricsService, evaluate_alert

NOW = 1_800_000_000.0


class TaxonomyTest(unittest.TestCase):
    def test_risk_control_beats_auth(self):
        text = 'HTTP 403: {"code":11140,"msg":"request illegal","displayMsg":"账号存在异常行为"}'
        self.assertEqual(tax.classify(text), tax.RISK_CONTROL)

    def test_rate_limit(self):
        self.assertEqual(tax.classify("HTTP 429: rate limit exceeded"), tax.RATE_LIMIT)
        self.assertEqual(tax.classify('code 6004 usage exceeds frequency limit'), tax.RATE_LIMIT)

    def test_auth(self):
        self.assertEqual(tax.classify("refresh_token_invalidated"), tax.AUTH)
        self.assertEqual(tax.classify("", status_code=401), tax.AUTH)

    def test_network(self):
        self.assertEqual(tax.classify("curl: (35) TLS connect error"), tax.NETWORK)
        self.assertEqual(tax.classify("connection timed out"), tax.NETWORK)

    def test_upstream_and_client(self):
        self.assertEqual(tax.classify("upstream returned 502"), tax.UPSTREAM)
        self.assertEqual(tax.classify("", status_code=503), tax.UPSTREAM)
        self.assertEqual(tax.classify("invalid request: missing model"), tax.CLIENT)

    def test_pool_unavailable(self):
        self.assertEqual(tax.classify("model 'gpt-5-6' is not available to any active account"), tax.POOL)
        self.assertEqual(tax.classify('{"code":"no_healthy_account"}'), tax.POOL)

    def test_success_has_no_category(self):
        self.assertEqual(tax.classify("", status="success"), "")

    def test_describe(self):
        row = tax.describe(tax.RATE_LIMIT)
        self.assertEqual(row["label"], "限流")
        self.assertTrue(row["hint"])


class MetricsTest(unittest.TestCase):
    def make(self):
        return MetricsService(max_records=100)

    def test_snapshot_aggregates(self):
        service = self.make()
        for index in range(4):
            service.record_call(
                endpoint="/v1/chat/completions",
                model="gpt-5-6",
                status="success",
                duration_ms=100 + index * 10,
                now=NOW - 10 + index,
            )
        service.record_call(
            endpoint="/v1/chat/completions",
            model="gpt-5-6",
            status="failed",
            duration_ms=50,
            error="HTTP 429 rate limit",
            now=NOW - 1,
        )
        snapshot = service.snapshot(window_seconds=3600, now=NOW)
        self.assertEqual(snapshot["total"], 5)
        self.assertEqual(snapshot["failed"], 1)
        self.assertAlmostEqual(snapshot["success_rate"], 0.8)
        self.assertEqual(snapshot["by_model"][0]["model"], "gpt-5-6")
        self.assertEqual(snapshot["by_error_category"][0]["category"], tax.RATE_LIMIT)
        self.assertEqual(len(snapshot["recent_failures"]), 1)
        self.assertIsNotNone(snapshot["p95_ms"])

    def test_window_filtering(self):
        service = self.make()
        service.record_call(endpoint="/v1/x", model="m", status="success", now=NOW - 7200)
        service.record_call(endpoint="/v1/x", model="m", status="success", now=NOW - 5)
        snapshot = service.snapshot(window_seconds=600, now=NOW)
        self.assertEqual(snapshot["total"], 1)

    def test_rpm_counts_last_minute(self):
        service = self.make()
        for offset in (5, 30, 90):
            service.record_call(endpoint="/v1/x", model="m", status="success", now=NOW - offset)
        self.assertEqual(service.snapshot(window_seconds=600, now=NOW)["rpm"], 2)

    def test_recent_is_newest_first(self):
        service = self.make()
        service.record_call(endpoint="/v1/a", model="m", status="success", now=NOW - 2)
        service.record_call(endpoint="/v1/b", model="m", status="success", now=NOW - 1)
        items = service.recent(10)
        self.assertEqual(items[0]["endpoint"], "/v1/b")


class AlertDecisionTest(unittest.TestCase):
    def settings(self, **kwargs):
        base = {
            "enabled": True,
            "webhook_url": "https://example.test/hook",
            "consecutive_failures": 5,
            "min_requests": 10,
            "max_failure_rate": 0.5,
            "qq": {},
        }
        base.update(kwargs)
        return base

    def test_disabled_or_no_channel(self):
        self.assertFalse(evaluate_alert(self.settings(enabled=False), None, 99))
        self.assertFalse(evaluate_alert(self.settings(webhook_url=""), None, 99))

    def test_consecutive_threshold(self):
        self.assertFalse(evaluate_alert(self.settings(), None, 4))
        self.assertTrue(evaluate_alert(self.settings(), None, 5))

    def test_failure_rate_threshold(self):
        snapshot = {"total": 20, "success_rate": 0.4}
        self.assertTrue(evaluate_alert(self.settings(consecutive_failures=99), snapshot, 1))

    def test_not_enough_requests_for_rate_rule(self):
        snapshot = {"total": 3, "success_rate": 0.0}
        self.assertFalse(evaluate_alert(self.settings(consecutive_failures=99), snapshot, 1))

    def test_qq_channel_counts(self):
        settings = self.settings(webhook_url="", qq={"url": "http://127.0.0.1:9999/send"})
        self.assertTrue(evaluate_alert(settings, None, 5))


if __name__ == "__main__":
    unittest.main()


class LogRotationTest(unittest.TestCase):
    def test_rotates_when_exceeding_limit(self):
        import tempfile
        from pathlib import Path

        from services.log_service import LogService

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "logs.jsonl"
            service = LogService(path, max_bytes=200)
            for index in range(20):
                service.add("call", f"entry-{index}", {"index": index})
            rotated = path.with_name(path.name + ".1")
            self.assertTrue(rotated.exists())
            self.assertLess(path.stat().st_size, 400)

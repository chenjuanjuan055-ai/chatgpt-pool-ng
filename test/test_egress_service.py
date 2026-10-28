"""Unit tests for the egress pool (services.egress_service)."""

from __future__ import annotations

import unittest

from services.egress_service import (
    EgressManager,
    EgressSession,
    is_transport_error,
)


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def settings(*endpoints, **kwargs):
    base = {
        "enabled": True,
        "auto_failover": True,
        "max_attempts": 3,
        "probe_url": "https://example.test/me",
        "probe_interval": 0,
        "timeout_sec": 5,
        "failure_threshold": 2,
        "cooldown_sec": 300,
        "endpoints": list(endpoints),
    }
    base.update(kwargs)
    return base


def endpoint(url: str, **kwargs):
    item = {"url": url, "label": url, "enabled": True, "weight": 1.0}
    item.update(kwargs)
    return item


class EgressManagerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.manager = EgressManager(probe_fn=lambda *_: (True, 5.0, "HTTP 401"), clock=self.clock)

    def test_apply_settings_dedupes_and_keeps_health(self) -> None:
        self.manager.apply_settings(settings(endpoint("http://a"), endpoint("http://a"), endpoint("http://b")))
        self.assertEqual([row["url"] for row in self.manager.snapshot()], ["http://a", "http://b"])
        self.manager.record_success("http://a", 10.0)
        self.manager.apply_settings(settings(endpoint("http://a"), endpoint("http://c")))
        rows = {row["url"]: row for row in self.manager.snapshot()}
        self.assertEqual(rows["http://a"]["latency_ms"], 10.0)
        self.assertNotIn("http://b", rows)

    def test_select_prefers_healthy_then_unknown_then_failed(self) -> None:
        self.manager.apply_settings(settings(endpoint("http://slow"), endpoint("http://fast"), endpoint("http://new")))
        self.manager.record_success("http://slow", 300.0)
        self.manager.record_success("http://fast", 40.0)
        self.assertEqual(self.manager.select(), "http://fast")
        self.manager.record_failure("http://fast", "boom")
        self.assertEqual(self.manager.select(), "http://slow")
        self.manager.record_failure("http://slow", "boom")
        self.assertEqual(self.manager.select(), "http://new")

    def test_failure_threshold_triggers_cooldown(self) -> None:
        self.manager.apply_settings(settings(endpoint("http://a"), endpoint("http://b")))
        self.manager.record_success("http://a", 10.0)
        self.manager.record_success("http://b", 20.0)
        self.manager.record_failure("http://b", "timeout")
        self.assertEqual(self.manager.select(), "http://a")  # healthy wins over a failed peer
        self.assertEqual({row["url"]: row for row in self.manager.snapshot()}["http://b"]["state"], "failed")
        self.manager.record_failure("http://b", "timeout")
        self.assertEqual(self.manager.select(), "http://a")
        rows = {row["url"]: row for row in self.manager.snapshot()}
        self.assertEqual(rows["http://b"]["state"], "cooling")
        self.assertGreater(rows["http://b"]["cooldown_remaining"], 0)
        self.clock.advance(301)
        self.assertIn(self.manager.select(), {"http://a", "http://b"})
        rows = {row["url"]: row for row in self.manager.snapshot()}
        self.assertNotEqual(rows["http://b"]["state"], "cooling")

    def test_record_success_clears_cooldown_and_failures(self) -> None:
        self.manager.apply_settings(settings(endpoint("http://a")))
        self.manager.record_failure("http://a", "x")
        self.manager.record_failure("http://a", "x")
        self.manager.record_success("http://a", 12.0)
        row = self.manager.snapshot()[0]
        self.assertEqual(row["consecutive_failures"], 0)
        self.assertEqual(row["state"], "healthy")

    def test_disabled_pool_selects_nothing(self) -> None:
        self.manager.apply_settings(settings(endpoint("http://a"), enabled=False))
        self.assertIsNone(self.manager.select())
        self.assertFalse(self.manager.enabled)

    def test_probe_updates_health(self) -> None:
        results = {"http://a": (True, 12.0, "HTTP 401"), "http://b": (False, 100.0, "connect timeout")}
        manager = EgressManager(probe_fn=lambda url, *_: results[url], clock=self.clock)
        manager.apply_settings(settings(endpoint("http://a"), endpoint("http://b"), failure_threshold=1))
        rows = {row["url"]: row for row in manager.probe_all()}
        self.assertEqual(rows["http://a"]["state"], "healthy")
        self.assertEqual(rows["http://a"]["latency_ms"], 12.0)
        self.assertEqual(rows["http://b"]["state"], "cooling")
        self.assertIn("connect timeout", rows["http://b"]["last_error"])

    def test_probe_exception_counts_as_failure(self) -> None:
        def boom(*_args):
            raise RuntimeError("probe crashed")

        manager = EgressManager(probe_fn=boom, clock=self.clock)
        manager.apply_settings(settings(endpoint("http://a"), failure_threshold=1))
        rows = manager.probe_all()
        self.assertEqual(rows[0]["state"], "cooling")
        self.assertIn("probe crashed", rows[0]["last_error"])


class FakeSession:
    def __init__(self, *, fail: bool = False, response: object | None = None) -> None:
        self.fail = fail
        self.response = response or {"ok": True}
        self.closed = False
        self.requests: list[tuple[str, str]] = []

    def request(self, method: str, url: str, **kwargs):
        self.requests.append((method, url))
        if self.fail:
            raise ConnectionError("tls connect error")
        return self.response

    def close(self) -> None:
        self.closed = True


class EgressSessionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.manager = EgressManager(clock=self.clock)
        self.manager.apply_settings(
            settings(endpoint("http://a", weight=2.0), endpoint("http://b", weight=1.0), failure_threshold=1)
        )
        self.manager.record_success("http://a", 5.0)
        self.manager.record_success("http://b", 9.0)

    def test_failover_retries_next_egress(self) -> None:
        created: list[FakeSession] = []

        def factory(**kwargs):
            session = FakeSession(fail=kwargs.get("proxy") == "http://a")
            session.proxy = kwargs.get("proxy")
            created.append(session)
            return session

        session = EgressSession(manager=self.manager, session_factory=factory, max_attempts=2)
        response = session.get("https://example.test/")
        self.assertEqual(response, {"ok": True})
        self.assertEqual([item.proxy for item in created], ["http://a", "http://b"])
        states = {row["url"]: row for row in self.manager.snapshot()}
        self.assertEqual(states["http://a"]["state"], "cooling")
        self.assertEqual(states["http://b"]["state"], "healthy")
        self.assertTrue(created[0].closed)

    def test_non_transport_errors_are_not_retried(self) -> None:
        calls: list[FakeSession] = []

        class Boom(FakeSession):
            def request(self, method: str, url: str, **kwargs):
                calls.append(self)
                raise ValueError("bad body")

        session = EgressSession(manager=self.manager, session_factory=lambda **_: Boom(), max_attempts=3)
        with self.assertRaises(ValueError):
            session.post("https://example.test/", json={})
        self.assertEqual(len(calls), 1)
        states = {row["url"]: row for row in self.manager.snapshot()}
        self.assertEqual(states["http://a"]["state"], "healthy")

    def test_direct_mode_without_pool_uses_base_proxy(self) -> None:
        manager = EgressManager()
        manager.apply_settings({"enabled": False, "endpoints": []})
        captured = {}

        def factory(**kwargs):
            captured.update(kwargs)
            return FakeSession()

        session = EgressSession(manager=manager, session_factory=factory, proxy_url="http://base", max_attempts=2)
        session.get("https://example.test/")
        self.assertEqual(captured.get("proxy"), "http://base")

    def test_transport_error_detection(self) -> None:
        self.assertTrue(is_transport_error(ConnectionError("x")))
        self.assertTrue(is_transport_error(TimeoutError("x")))
        self.assertFalse(is_transport_error(ValueError("x")))

        class HTTPError(ConnectionError):
            pass

        self.assertFalse(is_transport_error(HTTPError("x")))


if __name__ == "__main__":
    unittest.main()

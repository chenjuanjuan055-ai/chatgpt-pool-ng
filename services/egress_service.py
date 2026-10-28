"""Egress pool: health-aware proxy selection with automatic failover.

The pool keeps a small set of outbound proxy endpoints (typically local
mihomo/xray inbounds). Each endpoint has a health state maintained by
periodic probes and by real request outcomes. Requests select the
healthiest endpoint; transport failures are recorded and, after
``failure_threshold`` consecutive failures, the endpoint is cooled down.

All probe/session integrations are optional and imported lazily so this
module stays importable with the standard library only (unit tests).
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping

DEFAULT_PROBE_URL = "https://chatgpt.com/backend-api/me"
DEFAULT_PROBE_OK = frozenset({200, 401})
TRANSPORT_ERROR_NAMES = frozenset(
    {
        "ConnectionError",
        "ConnectError",
        "ConnectTimeout",
        "ReadTimeout",
        "Timeout",
        "TimeoutError",
        "RequestsError",
        "RequestException",
        "SSLError",
        "ProxyError",
        "SocketError",
        "CurlError",
    }
)


@dataclass
class EgressEndpoint:
    url: str
    label: str = ""
    enabled: bool = True
    weight: float = 1.0


@dataclass
class EgressHealth:
    healthy: bool | None = None
    latency_ms: float | None = None
    consecutive_failures: int = 0
    last_checked_at: float | None = None
    last_success_at: float | None = None
    last_error: str = ""
    cooldown_until: float = 0.0
    probe_detail: str = ""


def is_transport_error(exc: BaseException) -> bool:
    """True when the failure happened before a usable HTTP response."""
    for cls in type(exc).__mro__:
        if cls.__name__ == "HTTPError":
            return False
        if cls.__name__ in TRANSPORT_ERROR_NAMES:
            return True
    return isinstance(exc, (ConnectionError, TimeoutError, OSError))


class EgressManager:
    """Thread-safe endpoint registry with health tracking and probing."""

    def __init__(
        self,
        probe_fn: Callable[[str, str, float], tuple[bool, float | None, str]] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._probe_fn = probe_fn or default_probe
        self._clock = clock or time.time
        self._lock = threading.RLock()
        self._endpoints: list[EgressEndpoint] = []
        self._health: dict[str, EgressHealth] = {}
        self._settings: dict[str, Any] = {}
        self._signature = ""
        self._probe_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    # -- configuration ---------------------------------------------------
    @property
    def enabled(self) -> bool:
        with self._lock:
            return bool(self._settings.get("enabled")) and bool(self._endpoints)

    def apply_settings(self, settings: Mapping[str, Any] | None) -> None:
        settings = dict(settings or {})
        signature = json.dumps(settings, sort_keys=True, ensure_ascii=False, default=str)
        with self._lock:
            if signature == self._signature:
                return
            self._signature = signature
            self._settings = settings
            endpoints = []
            seen: set[str] = set()
            for item in settings.get("endpoints") or []:
                if not isinstance(item, dict):
                    continue
                url = str(item.get("url") or "").strip()
                if not url or url in seen:
                    continue
                seen.add(url)
                try:
                    weight = float(item.get("weight") or 1.0)
                except (TypeError, ValueError):
                    weight = 1.0
                endpoints.append(
                    EgressEndpoint(
                        url=url,
                        label=str(item.get("label") or "").strip(),
                        enabled=bool(item.get("enabled", True)),
                        weight=max(0.0, weight),
                    )
                )
            self._endpoints = endpoints
            known = {endpoint.url for endpoint in endpoints}
            self._health = {url: state for url, state in self._health.items() if url in known}
            for endpoint in endpoints:
                self._health.setdefault(endpoint.url, EgressHealth())
        self._sync_probe_thread()

    def _sync_probe_thread(self) -> None:
        interval = self._int_setting("probe_interval", 120)
        should_run = self.enabled and interval > 0
        with self._lock:
            running = self._probe_thread is not None and self._probe_thread.is_alive()
            if should_run and not running:
                self._stop_event.clear()
                self._probe_thread = threading.Thread(
                    target=self._probe_loop,
                    args=(interval,),
                    name="egress-probe",
                    daemon=True,
                )
                self._probe_thread.start()
            elif not should_run and running:
                self._stop_event.set()
                self._probe_thread = None

    def _int_setting(self, key: str, fallback: int) -> int:
        value = self._settings.get(key)
        if value is None or value == "":
            return fallback
        try:
            return int(value)
        except (TypeError, ValueError):
            return fallback

    # -- selection -------------------------------------------------------
    def select(self, exclude: Iterable[str] | None = None) -> str | None:
        """Return the best endpoint URL, or None when the pool is unusable."""
        excluded = set(exclude or ())
        now = self._clock()
        with self._lock:
            if not self._settings.get("enabled"):
                return None
            candidates = [e for e in self._endpoints if e.enabled and e.url not in excluded]
            if not candidates:
                return None
            available = []
            cooling = []
            for endpoint in candidates:
                health = self._health.setdefault(endpoint.url, EgressHealth())
                if health.cooldown_until and health.cooldown_until > now:
                    cooling.append((health.cooldown_until, endpoint))
                else:
                    available.append((endpoint, health))

            def sort_key(item: tuple[EgressEndpoint, EgressHealth]) -> tuple:
                endpoint, health = item
                if health.healthy is True:
                    rank = 0
                    latency = health.latency_ms if health.latency_ms is not None else float("inf")
                elif health.healthy is None:
                    rank = 1
                    latency = float("inf")
                else:
                    rank = 2
                    latency = health.latency_ms if health.latency_ms is not None else float("inf")
                return (rank, latency, -endpoint.weight, endpoint.url)

            if available:
                available.sort(key=sort_key)
                return available[0][0].url
            if cooling:
                cooling.sort(key=lambda item: item[0])
                return cooling[0][1].url
            return None

    # -- health updates --------------------------------------------------
    def record_success(self, url: str, latency_ms: float | None = None) -> None:
        if not url:
            return
        now = self._clock()
        with self._lock:
            health = self._health.setdefault(url, EgressHealth())
            health.healthy = True
            health.consecutive_failures = 0
            health.cooldown_until = 0.0
            health.last_checked_at = now
            health.last_success_at = now
            health.last_error = ""
            if latency_ms is not None:
                if health.latency_ms is None:
                    health.latency_ms = latency_ms
                else:
                    health.latency_ms = health.latency_ms * 0.7 + latency_ms * 0.3

    def record_failure(self, url: str, error: str = "") -> None:
        if not url:
            return
        now = self._clock()
        threshold = self._int_setting("failure_threshold", 2)
        cooldown = self._int_setting("cooldown_sec", 300)
        with self._lock:
            health = self._health.setdefault(url, EgressHealth())
            health.healthy = False
            health.consecutive_failures += 1
            health.last_checked_at = now
            health.last_error = str(error or "")[:500]
            if health.consecutive_failures >= threshold:
                health.cooldown_until = now + cooldown

    # -- probing ---------------------------------------------------------
    def probe_all(self, timeout: float | None = None) -> list[dict[str, Any]]:
        probe_url = str(self._settings.get("probe_url") or DEFAULT_PROBE_URL)
        wait = float(timeout if timeout is not None else self._int_setting("timeout_sec", 8))
        with self._lock:
            targets = [endpoint for endpoint in self._endpoints if endpoint.enabled]
        for endpoint in targets:
            ok, latency, detail = self._safe_probe(endpoint.url, probe_url, wait)
            if ok:
                self.record_success(endpoint.url, latency)
            else:
                self.record_failure(endpoint.url, detail)
            with self._lock:
                self._health.setdefault(endpoint.url, EgressHealth()).probe_detail = detail
        return self.snapshot()

    def _safe_probe(self, proxy_url: str, probe_url: str, timeout: float) -> tuple[bool, float | None, str]:
        try:
            return self._probe_fn(proxy_url, probe_url, timeout)
        except Exception as exc:  # pragma: no cover - defensive
            return False, None, str(exc)[:200]

    def _probe_loop(self, interval: int) -> None:
        while not self._stop_event.is_set():
            try:
                self.probe_all()
            except Exception:
                pass
            if self._stop_event.wait(interval):
                break

    def stop(self) -> None:
        self._stop_event.set()
        with self._lock:
            self._probe_thread = None

    # -- introspection ---------------------------------------------------
    def snapshot(self) -> list[dict[str, Any]]:
        now = self._clock()
        with self._lock:
            rows = []
            for endpoint in self._endpoints:
                health = self._health.setdefault(endpoint.url, EgressHealth())
                cooling = bool(health.cooldown_until and health.cooldown_until > now)
                rows.append(
                    {
                        "url": endpoint.url,
                        "label": endpoint.label,
                        "enabled": endpoint.enabled,
                        "weight": endpoint.weight,
                        "healthy": health.healthy,
                        "state": "cooling" if cooling else ("healthy" if health.healthy else ("unknown" if health.healthy is None else "failed")),
                        "latency_ms": round(health.latency_ms, 1) if health.latency_ms is not None else None,
                        "consecutive_failures": health.consecutive_failures,
                        "cooldown_until": health.cooldown_until if cooling else None,
                        "cooldown_remaining": round(health.cooldown_until - now, 1) if cooling else 0,
                        "last_checked_at": health.last_checked_at,
                        "last_success_at": health.last_success_at,
                        "last_error": health.last_error,
                        "probe_detail": health.probe_detail,
                    }
                )
            return rows

    def settings(self) -> dict[str, Any]:
        return dict(self._settings)


def default_probe(proxy_url: str, probe_url: str, timeout: float) -> tuple[bool, float | None, str]:
    """Probe one proxy endpoint. 200/401 mean the egress chain works."""
    start = time.perf_counter()
    try:
        from curl_cffi import requests as curl_requests  # type: ignore

        response = curl_requests.get(
            probe_url,
            proxy=proxy_url or None,
            timeout=timeout,
            verify=False,
            allow_redirects=False,
        )
        latency = (time.perf_counter() - start) * 1000
        ok = response.status_code in DEFAULT_PROBE_OK
        return ok, latency, f"HTTP {response.status_code}"
    except Exception as exc:
        latency = (time.perf_counter() - start) * 1000
        return False, latency, str(exc)[:200]


class EgressSession:
    """Duck-typed requests.Session that retries on another egress.

    The wrapped factory defaults to ``curl_cffi.requests.Session``. HTTP
    responses are returned as-is; only transport failures trigger failover.
    """

    def __init__(
        self,
        *,
        manager: EgressManager | None = None,
        session_factory: Callable[..., Any] | None = None,
        session_kwargs: Mapping[str, Any] | None = None,
        proxy_url: str = "",
        max_attempts: int = 3,
        auto_failover: bool = True,
        max_failover_attempts: int | None = None,
    ) -> None:
        self._manager = manager or egress_manager
        self._factory = session_factory
        self._kwargs = dict(session_kwargs or {})
        self._base_proxy = str(proxy_url or "").strip()
        self._attempts = max(1, int(max_failover_attempts or max_attempts or 1))
        self._auto = bool(auto_failover)
        self._inner: Any = None
        self._current_proxy = ""
        self._tried: set[str] = set()

    # -- session lifecycle ----------------------------------------------
    def _create(self) -> None:
        if self._inner is not None:
            return
        proxy = self._base_proxy
        if self._auto and self._manager is not None and self._manager.enabled:
            picked = self._manager.select(exclude=self._tried)
            if picked:
                proxy = picked
        kwargs = dict(self._kwargs)
        if proxy:
            kwargs["proxy"] = proxy
        factory = self._factory
        if factory is None:
            from curl_cffi.requests import Session as factory  # type: ignore
        self._inner = factory(**kwargs)
        self._current_proxy = proxy

    def _reset(self) -> None:
        inner, self._inner = self._inner, None
        if inner is not None:
            try:
                inner.close()
            except Exception:
                pass
        if self._current_proxy:
            self._tried.add(self._current_proxy)
        self._current_proxy = ""

    def close(self) -> None:
        inner, self._inner = self._inner, None
        if inner is not None:
            try:
                inner.close()
            except Exception:
                pass

    # -- request path ----------------------------------------------------
    def request(self, method: str, url: str, **kwargs: Any) -> Any:
        attempts = self._attempts if (self._auto and self._manager is not None and self._manager.enabled) else 1
        last_error: BaseException | None = None
        for _ in range(attempts):
            self._create()
            started = time.perf_counter()
            try:
                response = self._inner.request(method, url, **kwargs)
            except Exception as exc:
                if not is_transport_error(exc):
                    raise
                last_error = exc
                if self._current_proxy and self._manager is not None:
                    self._manager.record_failure(self._current_proxy, str(exc))
                self._reset()
                continue
            if self._current_proxy and self._manager is not None:
                self._manager.record_success(self._current_proxy, (time.perf_counter() - started) * 1000)
            return response
        if last_error is not None:
            raise last_error
        raise RuntimeError("egress request failed without attempts")

    def get(self, url: str, **kwargs: Any) -> Any:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> Any:
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> Any:
        return self.request("PUT", url, **kwargs)

    def patch(self, url: str, **kwargs: Any) -> Any:
        return self.request("PATCH", url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> Any:
        return self.request("DELETE", url, **kwargs)

    def head(self, url: str, **kwargs: Any) -> Any:
        return self.request("HEAD", url, **kwargs)

    def options(self, url: str, **kwargs: Any) -> Any:
        return self.request("OPTIONS", url, **kwargs)

    # -- delegation ------------------------------------------------------
    def __getattr__(self, name: str) -> Any:
        self._create()
        return getattr(self._inner, name)

    def __enter__(self) -> "EgressSession":
        self._create()
        return self

    def __exit__(self, *args: Any) -> bool:
        self.close()
        return False


egress_manager = EgressManager()

"""外部依赖端点的可达性探测与缓存。

背景
----
Windows 上连接**本机未监听**的端口时，内核会把 SYN 丢掉，要等 TCP 重传
超时（约 2 秒）才收到 RST。于是 Grok 控制台 / 注册控制台在外部服务没有
启动时，每个接口都会先空等 2 秒才返回 502。

处理方式
--------
后台线程定期探测已登记的外部端点，把结果缓存起来；业务请求先查缓存，
确知不可达就立刻失败，不再等待。缓存过期或尚未探测过（未知）时一律放行，
不会误伤正常链路。

用法::

    from services.endpoint_probe import endpoint_probe

    endpoint_probe.register("grok2api", base_url)
    if endpoint_probe.is_reachable("grok2api", base_url) is False:
        raise MyError("grok2api 未运行")
"""
from __future__ import annotations

import threading
import time
import urllib.error
import urllib.request
from typing import Any

# 探测请求自身的超时。故意压得比业务超时短很多——探测失败就快速失败。
_PROBE_TIMEOUT = 1.5
# 缓存有效期：超过这个时间视为「未知」，放行真实请求。
_CACHE_TTL = 10.0
# 后台探测间隔。
_PROBE_INTERVAL = 8.0


class EndpointProbe:
    """外部依赖端点的可达性探测与缓存。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, dict[str, Any]] = {}
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # ---------- 登记与查询 ----------
    def register(self, name: str, url: str) -> None:
        """登记端点。URL 变化时立即作废旧缓存，等价于「地址刚改过，状态未知」。"""
        url = (url or "").rstrip("/")
        with self._lock:
            current = self._state.get(name)
            if current is None or current.get("url") != url:
                self._state[name] = {
                    "url": url,
                    "reachable": None,
                    "checked_at": 0.0,
                    "latency_ms": 0.0,
                    "error": "",
                }

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """返回全部端点的探测快照（供 /healthz 等展示）。"""
        with self._lock:
            now = time.time()
            result: dict[str, dict[str, Any]] = {}
            for name, state in self._state.items():
                item = dict(state)
                age = now - item.get("checked_at", 0.0)
                item["age_sec"] = round(age, 1)
                item["stale"] = item.get("reachable") is not None and age > _CACHE_TTL
                result[name] = item
            return result

    def is_reachable(self, name: str, url: str) -> bool | None:
        """返回 True / False / None（未知）。

        None 表示尚未探测或缓存已过期，此时应当放行真实请求，不要误杀。
        """
        self.register(name, url)
        with self._lock:
            state = self._state.get(name) or {}
            reachable = state.get("reachable")
            if reachable is None:
                return None
            if time.time() - state.get("checked_at", 0.0) > _CACHE_TTL:
                return None
            return bool(reachable)

    def describe(self, name: str, url: str) -> str:
        """生成一句可读的不可达原因，给前端直接展示。"""
        self.register(name, url)
        with self._lock:
            state = self._state.get(name) or {}
            error = state.get("error") or "目标端口无响应"
            target = state.get("url") or url
        return f"{name} 未运行或不可达（{target}）：{error}"

    # ---------- 实际探测 ----------
    def probe_once(self, name: str, url: str) -> bool:
        """探测一次端点，返回是否可达。任何 HTTP 响应（含 401/404）都算可达。"""
        self.register(name, url)
        with self._lock:
            target = (self._state.get(name) or {}).get("url") or ""
        if not target:
            return False

        started = time.perf_counter()
        reachable = False
        error = ""
        try:
            request = urllib.request.Request(target, method="GET")
            with urllib.request.urlopen(request, timeout=_PROBE_TIMEOUT):
                reachable = True
        except urllib.error.HTTPError:
            # 服务有响应，只是状态码非 2xx —— 说明端口是活的。
            reachable = True
        except urllib.error.URLError as exc:
            error = str(getattr(exc, "reason", exc))
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"

        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        with self._lock:
            state = self._state.get(name)
            if state is not None:
                state["reachable"] = reachable
                state["checked_at"] = time.time()
                state["latency_ms"] = latency_ms
                state["error"] = "" if reachable else error
        return reachable

    # ---------- 后台线程 ----------
    def probe_all(self) -> None:
        """立即把所有已登记的端点探测一遍。"""
        for name, state in self.snapshot().items():
            url = state.get("url") or ""
            if not url:
                continue
            try:
                self.probe_once(name, url)
            except Exception:  # noqa: BLE001
                pass

    def _loop(self) -> None:
        while not self._stop.wait(_PROBE_INTERVAL):
            self.probe_all()
            if self._stop.is_set():
                return

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        # 先同步探一轮：后台线程要等一个间隔才跑，而第一个业务请求不该
        # 替它把「等 2 秒才知道对方没起来」这笔账付掉。
        self.probe_all()
        self._thread = threading.Thread(target=self._loop, name="endpoint-probe", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2)
        self._thread = None


endpoint_probe = EndpointProbe()

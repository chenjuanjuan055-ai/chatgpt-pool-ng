"""受管的 SSH 端口转发隧道。

场景
----
grok2api / minimax2api 这类号池网关只监听 VPS 上的 127.0.0.1，云安全组
又不放行对应端口（实测 47.80.60.216:8002 从公网连是 timeout）。本机要用
它们，只能自己拉一条 ``ssh -N -L`` 隧道。

为什么单独做成模块
------------------
手工开的隧道一断就没了，而且没有任何自愈。这里负责：

- 起一条 ``ssh -N -L local:remote host``，带 ``ExitOnForwardFailure`` 与
  ``ServerAliveInterval``
- 后台线程定期探活，隧道掉了自动重连
- 端口占用检测：本地端口已经有东西在听就复用，不重复拉

用法::

    tunnel = SshTunnel("grok2api", "root@1.2.3.4", 8002, 8002)
    tunnel.ensure()          # 不通就拉，通就直接用
    tunnel.alive()
    tunnel.stop()
"""
from __future__ import annotations

import socket
import subprocess
import threading
import time


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class SshTunnel:
    """一条 ssh -L 端口转发，带自动重连。"""

    def __init__(
        self,
        name: str,
        ssh_host: str,
        remote_port: int,
        local_port: int,
        keepalive: int = 30,
        check_interval: int = 20,
    ) -> None:
        self.name = name
        self.ssh_host = (ssh_host or "").strip()
        self.remote_port = int(remote_port or 0)
        self.local_port = int(local_port or 0)
        self.keepalive = keepalive
        self.check_interval = check_interval
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._watcher: threading.Thread | None = None
        self._stop = threading.Event()
        self.last_error = ""

    # ---------- 状态 ----------
    def process_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def alive(self) -> bool:
        """隧道是否可用：本地端口能连上就算通。"""
        return self.local_port > 0 and port_open(self.local_port)

    # ---------- 控制 ----------
    def start(self, wait_sec: float = 8.0) -> bool:
        """确保隧道在跑。返回是否可用。"""
        if not self.ssh_host or self.remote_port <= 0 or self.local_port <= 0:
            self.last_error = "隧道配置不完整"
            return False
        if self.alive():
            return True
        with self._lock:
            if self.process_alive():
                # 进程在但端口还没通，等一下再看。
                pass
            else:
                cmd = [
                    "ssh", "-N",
                    "-o", "BatchMode=yes",
                    "-o", "ExitOnForwardFailure=yes",
                    "-o", f"ServerAliveInterval={self.keepalive}",
                    "-o", "ServerAliveCountMax=3",
                    "-L", f"{self.local_port}:127.0.0.1:{self.remote_port}",
                    self.ssh_host,
                ]
                try:
                    self._proc = subprocess.Popen(
                        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                    self.last_error = ""
                except Exception as exc:
                    self._proc = None
                    self.last_error = f"{type(exc).__name__}: {exc}"
                    return False
        deadline = time.time() + wait_sec
        while time.time() < deadline:
            if self.alive():
                self._ensure_watcher()
                return True
            if self._proc is not None and self._proc.poll() is not None:
                self.last_error = "ssh 进程已退出"
                break
            time.sleep(0.4)
        self.last_error = self.last_error or "隧道未能在本地端口就绪"
        return False

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=3)
                except Exception:
                    self._proc.kill()
            self._proc = None
        self._watcher = None

    # ---------- 保活 ----------
    def _ensure_watcher(self) -> None:
        if self._watcher is not None and self._watcher.is_alive():
            return
        self._stop.clear()
        self._watcher = threading.Thread(target=self._loop, name=f"ssh-tunnel-{self.name}", daemon=True)
        self._watcher.start()

    def _loop(self) -> None:
        while not self._stop.wait(self.check_interval):
            if self.alive():
                continue
            # 端口不通：可能是 ssh 掉了，重连一次。
            with self._lock:
                if self._proc is not None and self._proc.poll() is None:
                    self._proc.terminate()
                self._proc = None
            self.start(wait_sec=6.0)

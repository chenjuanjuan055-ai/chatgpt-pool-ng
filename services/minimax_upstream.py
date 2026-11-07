"""已并入 services/minimax_console.py。

保留这个模块名是为了不打断既有引用；MiniMax 控制台现在完全由
``services.minimax_console`` 提供（连接管理、SSH 隧道、admin API 代理）。
"""
from __future__ import annotations

from services.minimax_console import MinimaxConsole, MinimaxConsoleError, minimax_console

# 旧名保留为别名
minimax_upstream = minimax_console

__all__ = ["MinimaxConsole", "MinimaxConsoleError", "minimax_console", "minimax_upstream"]

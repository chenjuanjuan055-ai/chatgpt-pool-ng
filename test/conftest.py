"""pytest 全局配置。

两件事：

1. **隔离 live 用例**——带 ``live`` 标记的测试会打真实上游、消耗真实账号额度
   （一次生图就把号池配额扣掉几个）。默认跳过，需要时显式开启：

   .. code-block:: powershell

       $env:CHATGPT2API_LIVE = "1"; python -m pytest test -m live

   只跑离线用例：

   .. code-block:: powershell

       python -m pytest test

2. **兜底超时**——上游 SSE 有时不结束，客户端 ``iter_lines()`` 会一直等，
   整套测试就挂在那里不动了。超时时间在 ``pyproject.toml`` 的
   ``[tool.pytest.ini_options]`` 里配置。
"""
from __future__ import annotations

import os

import pytest

LIVE_ENV = "CHATGPT2API_LIVE"

# 测试用统一的鉴权密钥。必须在这里（而不是各测试文件里）设置：
# 各文件用 os.environ.setdefault 设置，而 pytest 按文件名顺序导入模块，
# 先导入的文件会把自己的值定死，后面文件里的 setdefault 就全部失效，
# 于是「Bearer chatgpt2api」的请求被鉴权拒绝（401）。
# conftest.py 先于所有 test 模块被导入，所以这里设置的值一定生效。
TEST_AUTH_KEY = "chatgpt2api"
os.environ["CHATGPT2API_AUTH_KEY"] = TEST_AUTH_KEY


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "live: 需要真实上游的集成测试，会消耗真实账号额度（需 CHATGPT2API_LIVE=1）",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get(LIVE_ENV) == "1":
        return
    skip_live = pytest.mark.skip(reason=f"live 测试会消耗真实账号额度，设置 {LIVE_ENV}=1 才运行")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)

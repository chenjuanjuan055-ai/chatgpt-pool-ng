#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Grok 设备码登录：生成验证码 → 你在 x.ai 授权 → 自动轮询到完成。

用法（在项目根目录）：
    python scripts/grok_device_login.py

运行时输入邮箱和密码（不回显），脚本会：
1. 调本机控制台 /api/grok/accounts/device/start 拿 userCode
2. 把授权地址和验证码打印出来
3. 每 5 秒轮询一次，直到状态变成完成/失败
"""
from __future__ import annotations

import getpass
import json
import sys
import time
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8000"


def auth_key() -> str:
    data = json.loads((Path(__file__).resolve().parents[1] / "config.json").read_text(encoding="utf-8"))
    return str(data.get("auth-key") or "")


def call(path: str, payload: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json", "authorization": f"Bearer {auth_key()}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")


def main() -> int:
    email = input("Grok 账号邮箱: ").strip()
    password = getpass.getpass("密码（不回显）: ").strip()
    if not email or not password:
        print("邮箱/密码不能为空")
        return 2

    status, data = call("/api/grok/accounts/device/start", {"email": email, "password": password})
    if status != 200:
        print("发起失败:", json.dumps(data, ensure_ascii=False)[:300])
        return 1

    session_id = str(data.get("sessionId") or "")
    code = str(data.get("userCode") or "")
    uri = str(data.get("verificationUri") or data.get("verificationUriComplete") or "")
    interval = int(data.get("intervalSeconds") or 5)
    print("\n=== 请在浏览器打开下面地址，输入验证码完成授权 ===")
    print("地址:", uri)
    print("验证码:", code)
    print("===============================================\n")

    deadline = time.time() + 600
    last = ""
    while time.time() < deadline:
        time.sleep(interval)
        status, result = call("/api/grok/accounts/device/poll", {"session_id": session_id})
        state = str(result.get("status") or result.get("state") or "")
        if state != last:
            print(f"[轮询] {state or result}")
            last = state
        if state and state.lower() not in ("pending", "waiting", "authorization_pending"):
            print("\n最终结果:", json.dumps(result, ensure_ascii=False)[:500])
            return 0 if state.lower() in ("completed", "success", "authorized", "done") else 1
    print("超时（10 分钟）未完成授权")
    return 1


if __name__ == "__main__":
    sys.exit(main())

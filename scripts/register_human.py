#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""拟人化注册：真 Chrome + 持久化 profile + 真实鼠标轨迹/打字节奏。

用于替代之前被风控识别的那套（默认 Chromium + fill/click）：
- channel="chrome" 走本机安装版，指纹更真
- 持久化 user-data-dir，有完整 profile
- 每个操作都有鼠标移动轨迹、随机停顿、按字符输入
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.totp_bind_service import (  # noqa: E402
    EMAIL_SELECTORS, OTP_SELECTORS, PASSWORD_SELECTORS,
    enroll_and_activate, fetch_mail_code, page_text, read_session_token, wait_cloudflare,
)
from services.totp_service import code as totp_code  # noqa: E402


def pause(low: float = 0.4, high: float = 1.2) -> None:
    time.sleep(random.uniform(low, high))


def human_click(page, locator) -> bool:
    try:
        box = locator.bounding_box()
        if not box:
            locator.click()
            return True
        x = box["x"] + box["width"] * random.uniform(0.35, 0.65)
        y = box["y"] + box["height"] * random.uniform(0.35, 0.65)
        page.mouse.move(x + random.uniform(-40, 40), y + random.uniform(-30, 30), steps=random.randint(8, 18))
        pause(0.15, 0.4)
        page.mouse.move(x, y, steps=random.randint(6, 14))
        pause(0.08, 0.25)
        page.mouse.down()
        pause(0.05, 0.14)
        page.mouse.up()
        pause(0.3, 0.9)
        return True
    except Exception:
        try:
            locator.click()
            return True
        except Exception:
            return False


def human_type(page, locator, text: str) -> bool:
    try:
        if not human_click(page, locator):
            return False
        pause(0.2, 0.6)
        for char in text:
            page.keyboard.type(char)
            time.sleep(random.uniform(0.05, 0.16))
            if random.random() < 0.08:
                time.sleep(random.uniform(0.2, 0.6))
        pause(0.3, 0.8)
        return True
    except Exception:
        return False


def find(page, selectors, timeout_ms=25000):
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        for selector in selectors:
            try:
                loc = page.locator(selector)
                if loc.count() and loc.first.is_visible():
                    return loc.first
            except Exception:
                continue
        page.wait_for_timeout(300)
    return None


def button(page, name: str, timeout_ms=10000):
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        for loc in page.get_by_role("button", name=name, exact=True).all():
            try:
                if loc.is_visible():
                    return loc
            except Exception:
                continue
        page.wait_for_timeout(300)
    return None


def click_continue(page):
    for name in ("Continue", "继续", "Next", "下一步"):
        btn = button(page, name, 1500)
        if btn is not None:
            return human_click(page, btn)
    return False


def pool_call(base: str, key: str, path: str, payload: dict | None = None, method: str = "POST"):
    import urllib.request
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(base.rstrip("/") + path, data=data,
                                 headers={"content-type": "application/json", "authorization": f"Bearer {key}"},
                                 method=method)
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("email")
    parser.add_argument("--mail-api", default="http://127.0.0.1:19878")
    parser.add_argument("--proxy", default="http://127.0.0.1:7911")
    parser.add_argument("--profile", default=r"D:\xi\cgpt-pool-ng\_tmp\chrome-profile")
    parser.add_argument("--pool", default="http://127.0.0.1:8000")
    parser.add_argument("--key", default="")
    parser.add_argument("--out", default="_tmp/human_register.jsonl")
    args = parser.parse_args()

    if not args.key:
        try:
            args.key = str(json.loads((Path(__file__).resolve().parents[1] / "config.json")
                                      .read_text(encoding="utf-8")).get("auth-key") or "")
        except Exception:
            args.key = ""

    from playwright.sync_api import sync_playwright

    email = args.email
    name = "Melissa Thomas"
    age = "29"
    result = {"email": email, "state": "failed", "reason": ""}
    started = time.time()

    with sync_playwright() as p:
        profile = Path(args.profile)
        profile.mkdir(parents=True, exist_ok=True)
        context = p.chromium.launch_persistent_context(
            str(profile),
            channel="chrome",
            headless=False,
            proxy={"server": args.proxy},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport={"width": 1366, "height": 820},
            args=["--disable-blink-features=AutomationControlled", "--start-maximized"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=60000)
            wait_cloudflare(page)
            pause(1, 2)
            page.mouse.move(random.randint(200, 900), random.randint(150, 500), steps=random.randint(10, 25))
            pause(0.5, 1.2)
            page.goto("https://chatgpt.com/auth/login", wait_until="domcontentloaded", timeout=60000)
            wait_cloudflare(page)

            email_input = find(page, EMAIL_SELECTORS)
            if email_input is None:
                result["reason"] = "login_input_missing"
                return 1
            human_type(page, email_input, email)
            click_continue(page)

            for _ in range(40):
                page.wait_for_timeout(500)
                if "auth.openai.com" in page.url:
                    break
            pause(1.5, 2.5)
            text = page_text(page)
            if "创建你的账户" not in text:
                for _ in range(10):
                    page.wait_for_timeout(800)
                    text = page_text(page)
                    if text.strip():
                        break
            result["created"] = "创建你的账户" in text
            print("created:", result["created"], "|", text[:60].replace("\n", " | "))

            resend = page.get_by_text("重新发送电子邮件").first
            if resend.count():
                human_click(page, resend)
                pause(2, 3)
            since = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
            code = fetch_mail_code(args.mail_api, email, since, 150)
            if not code:
                result["reason"] = "no_mail_code"
                return 1
            print("code:", code)
            otp = find(page, OTP_SELECTORS, 15000)
            if otp is None:
                result["reason"] = "no_otp_input"
                return 1
            human_type(page, otp, code)
            click_continue(page)

            password_set = False
            for _ in range(10):
                pause(2, 3)
                url = page.url
                text = page_text(page)
                if "chatgpt.com" in url and "/auth/" not in url:
                    break
                if "about-you" in url or "你的年龄" in text or "全名" in text:
                    name_input = find(page, ('input[name="name"]',), 15000)
                    age_input = find(page, ('input[name="age"]', 'input[type="number"]'), 8000)
                    if name_input is None or age_input is None:
                        result["reason"] = "about_you_fields"
                        return 1
                    human_type(page, name_input, name)
                    human_type(page, age_input, age)
                    click_continue(page)
                    print("about-you filled")
                    continue
                pwd = find(page, PASSWORD_SELECTORS, 1200)
                if pwd is not None:
                    pw = "Aa1!" + "".join(random.choice("abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(12))
                    for index in range(min(page.locator('input[type="password"]').count(), 2)):
                        human_type(page, page.locator('input[type="password"]').nth(index), pw)
                    click_continue(page)
                    password_set = True
                    result["password"] = pw
                    continue

            if "chatgpt.com" not in page.url:
                page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=45000)
                pause(2, 3)
            token = ""
            for _ in range(20):
                token = read_session_token(page)
                if token:
                    break
                pause(1, 1.5)
            if not token:
                result["reason"] = "no_session"
                return 1

            bound = enroll_and_activate(page, token)
            if not bound.get("ok"):
                result["reason"] = f"bind_{bound.get('stage')}"
                return 1
            result.update(state="ok", secret=bound["secret"])

            start = pool_call(args.pool, args.key, "/api/accounts/oauth/start", {"email_hint": email})
            authorize_url = start.get("authorize_url") or start.get("url")
            page.goto(authorize_url, wait_until="commit", timeout=45000)
            callback = ""
            for _ in range(30):
                pause(0.5, 0.8)
                if "code=" in page.url and "/auth/callback" in page.url:
                    callback = page.url
                    break
            if callback:
                pool_call(args.pool, args.key, "/api/accounts/oauth/finish",
                          {"session_id": start.get("session_id"), "callback": callback})
                items = pool_call(args.pool, args.key, "/api/accounts", method="GET").get("items") or []
                target = next((a for a in items if str(a.get("email") or "").lower() == email.lower()), None)
                if target:
                    updates = {"access_token": target["access_token"], "totp_secret": bound["secret"]}
                    if result.get("password"):
                        updates["password"] = result["password"]
                    pool_call(args.pool, args.key, "/api/accounts/update", updates)
                    result["imported"] = True
            else:
                result["imported"] = False
            return 0 if result["state"] == "ok" else 1
        finally:
            try:
                print("elapsed", round(time.time() - started), "s")
                print(json.dumps({k: v for k, v in result.items() if k != "password"}, ensure_ascii=False))
                with open(args.out, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(result, ensure_ascii=False) + "\n")
            except Exception:
                pass
            context.close()


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""批量判断邮箱在 OpenAI 侧的状态：未注册 / 已注册无 2FA / 已注册已绑 2FA。

用法：
    python scripts/probe_openai_accounts.py emails.txt --out probe.jsonl
    python scripts/probe_openai_accounts.py emails.txt --limit 5 --proxy http://127.0.0.1:7911

结果逐行追加到 --out（JSONL），已探测过的邮箱自动跳过，可断点续跑。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.totp_bind_service import (  # noqa: E402
    EMAIL_SELECTORS,
    OTP_SELECTORS,
    PASSWORD_SELECTORS,
    click_continue,
    fetch_mail_code,
    first_visible,
    is_mfa_page,
    page_text,
    wait_cloudflare,
)


def classify_page(page, email: str, mail_api: str, log) -> dict:
    page.goto("https://chatgpt.com/auth/login", wait_until="domcontentloaded", timeout=60000)
    wait_cloudflare(page)
    email_input = first_visible(page, EMAIL_SELECTORS, 30000)
    if email_input is None:
        return {"state": "unknown", "reason": "login_page_unavailable", "url": page.url}
    email_input.fill(email)
    click_continue(page)

    deadline = time.time() + 30
    while time.time() < deadline:
        page.wait_for_timeout(500)
        if "auth.openai.com" in page.url:
            break

    # 等真正的登录步骤渲染出来（SPA + Cloudflare 可能需要十几秒）
    text = ""
    for _ in range(40):
        page.wait_for_timeout(500)
        text = page_text(page)
        url = page.url
        if any(marker in text for marker in ("检查你的收件箱", "创建你的账户", "创建密码", "输入密码", "验证码")):
            break
        if otp := first_visible(page, OTP_SELECTORS, 200):
            text = "验证码"
            break
    url = page.url

    if "创建密码" in text or "create-account" in url or "创建你的账户" in text:
        return {"state": "unregistered", "url": url}

    # 有密码登录页 → 切到一次性验证码登录，方便后续判断 2FA
    password_input = first_visible(page, PASSWORD_SELECTORS, 2000)
    if password_input is not None:
        for text_hint in ("使用一次性验证码登录", "使用一次性验证码", "Use a one-time code", "one-time code"):
            try:
                link = page.get_by_text(text_hint, exact=False)
                if link.count() and link.first.is_visible():
                    link.first.click()
                    page.wait_for_timeout(2500)
                    break
            except Exception:
                continue

    otp_input = first_visible(page, OTP_SELECTORS, 12000)
    if otp_input is None:
        return {"state": "unknown", "reason": "no_otp_input", "url": url, "text": text[:120]}

    try:
        resend = page.get_by_text("重新发送电子邮件").first
        if resend.count():
            resend.click()
            page.wait_for_timeout(2500)
    except Exception:
        pass
    log("  等待验证码")
    since = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    code = fetch_mail_code(mail_api, email, since, 120)
    if not code:
        return {"state": "unknown", "reason": "no_mail_code", "url": url}
    log(f"  验证码 {code}")
    otp_input.fill(code)
    click_continue(page)

    for _ in range(30):
        page.wait_for_timeout(1000)
        if is_mfa_page(page) or ("chatgpt.com" in page.url and "/auth/" not in page.url):
            break
        if "密码" in page_text(page) and "验证码" in page_text(page):
            continue

    if is_mfa_page(page):
        return {"state": "registered_2fa", "url": page.url, "text": page_text(page)[:80]}

    if "chatgpt.com" in page.url:
        return {"state": "registered_no_2fa", "url": page.url}

    return {"state": "unknown", "reason": "post_otp_page", "url": page.url, "text": page_text(page)[:120]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("emails", help="每行一个邮箱")
    parser.add_argument("--out", default="probe_results.jsonl")
    parser.add_argument("--mail-api", default="http://127.0.0.1:19878")
    parser.add_argument("--proxy", default="http://127.0.0.1:7911")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--retries", type=int, default=1)
    args = parser.parse_args()

    emails = [line.strip() for line in Path(args.emails).read_text(encoding="utf-8").splitlines() if line.strip()]
    out_path = Path(args.out)
    done = set()
    if out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            try:
                done.add(str(json.loads(line).get("email") or "").lower())
            except Exception:
                continue
    pending = [email for email in emails if email.lower() not in done]
    if args.limit:
        pending = pending[: args.limit]
    print(f"待探测 {len(pending)} / 总计 {len(emails)}，已完成 {len(done)}")

    from playwright.sync_api import sync_playwright

    started = time.time()
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            proxy={"server": args.proxy} if args.proxy else None,
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            for index, email in enumerate(pending, 1):
                result = {"email": email, "state": "unknown", "reason": "not_run", "at": time.time()}
                for attempt in range(args.retries + 1):
                    context = browser.new_context(locale="zh-CN", timezone_id="Asia/Shanghai",
                                                  viewport={"width": 1280, "height": 900})
                    page = context.new_page()
                    try:
                        outcome = classify_page(page, email, args.mail_api, print)
                        result.update(outcome)
                        break
                    except Exception as exc:
                        result.update(state="unknown", reason=f"{type(exc).__name__}: {exc}"[:160])
                    finally:
                        try:
                            context.close()
                        except Exception:
                            pass
                elapsed = time.time() - started
                print(f"[{index}/{len(pending)}] {email} -> {result['state']} ({result.get('reason','')}) {elapsed:.0f}s", flush=True)
                with out_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(result, ensure_ascii=False) + "\n")
        finally:
            browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""批量注册 OpenAI 网页账号：邮箱验证码 → 姓名/年龄 → （如需）设密码 → 绑定 TOTP。

用法：
    python scripts/register_openai_accounts.py emails.txt --out register.jsonl
    python scripts/register_openai_accounts.py emails.txt --limit 1 --dump
"""
from __future__ import annotations

import argparse
import json
import random
import secrets
import string
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.totp_service import code as totp_code  # noqa: E402
from services.totp_bind_service import (  # noqa: E402
    EMAIL_SELECTORS,
    OTP_SELECTORS,
    PASSWORD_SELECTORS,
    click_continue,
    enroll_and_activate,
    fetch_mail_code,
    first_visible,
    page_text,
    read_session_token,
    wait_cloudflare,
)

FIRST_NAMES = ("James", "Olivia", "Daniel", "Sophia", "Michael", "Emma", "William", "Ava",
               "Henry", "Mia", "Alexander", "Charlotte", "Benjamin", "Amelia", "Lucas", "Harper")
LAST_NAMES = ("Miller", "Wilson", "Taylor", "Anderson", "Thomas", "Jackson", "White",
              "Harris", "Martin", "Thompson", "Garcia", "Martinez", "Robinson", "Clark")


def new_password(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    while True:
        value = "".join(secrets.choice(alphabet) for _ in range(length))
        if (any(c.islower() for c in value) and any(c.isupper() for c in value)
                and any(c.isdigit() for c in value) and any(c in "!@#$%^&*" for c in value)):
            return value


def new_identity() -> tuple[str, str]:
    return f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}", str(random.randint(24, 38))


def set_input_value(page, selector: str, value: str) -> bool:
    """先正常 fill，失败则用原生 setter + input 事件（绕开不可见/被遮挡的控件）。"""
    try:
        page.wait_for_selector(selector, state="visible", timeout=15000)
        page.fill(selector, value, timeout=8000)
        return True
    except Exception:
        pass
    try:
        return bool(page.evaluate(
            """({sel, value}) => {
                const el = document.querySelector(sel);
                if (!el) return false;
                const desc = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
                desc.set.call(el, value);
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
                return true;
            }""",
            {"sel": selector, "value": value},
        ))
    except Exception:
        return False


def dismiss_banners(page) -> None:
    for label in ("接受所有", "全部接受", "接受", "Accept all", "Accept", "同意"):
        try:
            button = page.get_by_role("button", name=label, exact=True)
            if button.count() and button.first.is_visible():
                button.first.click(timeout=3000)
                page.wait_for_timeout(500)
                return
        except Exception:
            continue


def fill_name_age(page, name: str, age: str) -> bool:
    page.wait_for_timeout(1000)
    dismiss_banners(page)
    ok_name = set_input_value(page, 'input[name="name"]', name) or set_input_value(page, 'input[placeholder*="全名"]', name)
    ok_age = set_input_value(page, 'input[name="age"]', age) or set_input_value(page, 'input[type="number"]', age)
    if not (ok_name and ok_age):
        return False
    page.wait_for_timeout(500)
    click_continue(page)
    return True


def pool_call(base: str, key: str, path: str, payload: dict | None = None, method: str = "POST"):
    import urllib.request

    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        base.rstrip("/") + path,
        data=data,
        headers={"content-type": "application/json", "authorization": f"Bearer {key}"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def oauth_import(page, base: str, key: str, email: str, secret: str, password: str, log) -> bool:
    """借当前已登录的浏览器会话走一次 OAuth，把 RT 落进号池。"""
    try:
        start = pool_call(base, key, "/api/accounts/oauth/start", {"email_hint": email})
    except Exception as exc:
        log(f"  OAuth start 失败：{exc}")
        return False
    authorize_url = str(start.get("authorize_url") or start.get("url") or "")
    session_id = str(start.get("session_id") or "")
    if not authorize_url:
        log(f"  OAuth start 没返回 URL：{str(start)[:120]}")
        return False
    try:
        page.goto(authorize_url, wait_until="commit", timeout=45000)
    except Exception:
        pass
    callback = ""
    for _ in range(30):
        page.wait_for_timeout(500)
        current = page.url
        if "code=" in current and "/auth/callback" in current:
            callback = current
            break
    if not callback:
        log(f"  没拿到 OAuth callback：{page.url[:80]}")
        return False
    try:
        pool_call(base, key, "/api/accounts/oauth/finish", {"session_id": session_id, "callback": callback})
    except Exception as exc:
        log(f"  OAuth finish 失败：{exc}")
        return False
    try:
        items = pool_call(base, key, "/api/accounts", method="GET").get("items") or []
        target = next((a for a in items if str(a.get("email") or "").lower() == email.lower()), None)
        if target:
            updates = {"access_token": target["access_token"], "totp_secret": secret}
            if password:
                updates["password"] = password
            pool_call(base, key, "/api/accounts/update", updates)
        else:
            log("  导入成功但号池里没找到对应邮箱，密钥需手动补")
    except Exception as exc:
        log(f"  回写密钥失败：{exc}")
        return False
    return True


def register_one(browser, email: str, mail_api: str, log, pool_base: str = "", pool_key: str = "", existing_secret: str = "", import_only: bool = False) -> dict:
    password = new_password()
    name, age = new_identity()
    result = {"email": email, "state": "failed", "reason": "", "password": "", "secret": "", "name": name}

    context = browser.new_context(locale="zh-CN", timezone_id="Asia/Shanghai",
                                  viewport={"width": 1280, "height": 900})
    page = context.new_page()
    try:
        page.goto("https://chatgpt.com/auth/login", wait_until="domcontentloaded", timeout=60000)
        wait_cloudflare(page)
        email_input = first_visible(page, EMAIL_SELECTORS, 30000)
        if email_input is None:
            result["reason"] = "login_page_unavailable"
            return result
        email_input.fill(email)
        click_continue(page)

        for _ in range(40):
            page.wait_for_timeout(500)
            if "auth.openai.com" in page.url:
                break
        page.wait_for_timeout(2500)
        text = page_text(page)
        if "创建你的账户" not in text and "验证码" not in text:
            page.wait_for_timeout(8000)
            text = page_text(page)
        # 有「即可创建你的账户」= 新号走注册；没有 = 已有账号，直接登录补绑
        result["created"] = "创建你的账户" in text
        if result["created"]:
            log("  新号注册")
        else:
            log("  已有账号，直接登录")

        try:
            resend = page.get_by_text("重新发送电子邮件").first
            if resend.count():
                resend.click()
                page.wait_for_timeout(2500)
        except Exception:
            pass
        if "无法向你发送验证码" in page_text(page):
            result["reason"] = "otp_throttled"
            return result
        log("  等待验证码")
        since = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        code = fetch_mail_code(mail_api, email, since, 120)
        if not code:
            result["reason"] = "no_mail_code"
            return result
        log(f"  验证码 {code}")
        otp_input = first_visible(page, OTP_SELECTORS, 15000)
        if otp_input is None:
            result["reason"] = "no_otp_input"
            return result
        otp_input.fill(code)
        click_continue(page)
        page.wait_for_timeout(4000)
        if first_visible(page, OTP_SELECTORS, 1500) is not None and "验证码" in page_text(page):
            log("  验证码未通过，换新码重试")
            try:
                resend = page.get_by_text("重新发送电子邮件").first
                if resend.count():
                    resend.click()
                    page.wait_for_timeout(3000)
            except Exception:
                pass
            retry_since = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
            retry_code = fetch_mail_code(mail_api, email, retry_since, 120)
            if retry_code and retry_code != code:
                retry_input = first_visible(page, OTP_SELECTORS, 8000)
                if retry_input is not None:
                    retry_input.fill(retry_code)
                    click_continue(page)
                    log(f"  重试验证码 {retry_code}")

        # 已有 2FA 的账号：等 mfa-challenge 出现后用已知密钥过
        for _ in range(25):
            page.wait_for_timeout(1000)
            if "mfa" in page.url or ("chatgpt.com" in page.url and "/auth/" not in page.url):
                break
        if "mfa" in page.url:
            if not existing_secret:
                result["reason"] = "mfa_without_secret"
                return result
            log("  用已知密钥通过 2FA")
            mfa_input = first_visible(page, OTP_SELECTORS, 15000)
            if mfa_input is None:
                result["reason"] = "mfa_input_missing"
                return result
            mfa_input.fill(totp_code(existing_secret))
            click_continue(page)
            page.wait_for_timeout(3000)

        # 逐步走完注册引导
        password_set = False
        for _ in range(8):
            page.wait_for_timeout(3000)
            url = page.url
            text = page_text(page)
            if "chatgpt.com" in url and "/auth/" not in url:
                break
            if "about-you" in url or "你的年龄" in text or "全名" in text:
                if not fill_name_age(page, name, age):
                    try:
                        page.screenshot(path=r"D:\xi\Documents\Ai01\_tmp\about_you_fail.png", full_page=True)
                    except Exception:
                        pass
                    result["reason"] = "about_you_form_not_found"
                    return result
                log("  填写姓名/年龄")
                continue
            pwd = first_visible(page, PASSWORD_SELECTORS, 2000)
            if pwd is not None:
                inputs = page.locator('input[type="password"]')
                for index in range(min(inputs.count(), 2)):
                    inputs.nth(index).fill(password)
                click_continue(page)
                password_set = True
                log("  设置密码")
                continue
            if "验证码" in text and first_visible(page, OTP_SELECTORS, 500) is not None:
                result["reason"] = "otp_again"
                return result

        if "chatgpt.com" not in page.url:
            try:
                page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=45000)
            except Exception:
                pass

        token = ""
        for _ in range(30):
            token = read_session_token(page)
            if token:
                break
            page.wait_for_timeout(1000)
        if not token:
            result["reason"] = "no_session"
            return result

        if import_only:
            if not existing_secret:
                result["reason"] = "import_only_without_secret"
                return result
            bound = {"ok": True, "secret": existing_secret}
        else:
            bound = enroll_and_activate(page, token)
            if not bound.get("ok"):
                result["reason"] = f"bind_{bound.get('stage')}"
                return result

        result.update(state="ok", password=password if password_set else "", secret=bound["secret"])
        if pool_base and pool_key:
            log("  导入号池")
            result["imported"] = oauth_import(page, pool_base, pool_key, email, bound["secret"],
                                              password if password_set else "", log)
        return result
    finally:
        try:
            context.close()
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("emails")
    parser.add_argument("--out", default="register_results.jsonl")
    parser.add_argument("--mail-api", default="http://127.0.0.1:19878")
    parser.add_argument("--proxy", default="http://127.0.0.1:7911")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dump", action="store_true", help="只跑第一个并打印页面结构")
    parser.add_argument("--import-only", action="store_true", help="只做 OAuth 导入，不重新绑定（用已有密钥登录）")
    parser.add_argument("--pool", default="http://127.0.0.1:8000", help="号池控制台地址，留空不改")
    parser.add_argument("--key", default="", help="号池管理员 key")
    args = parser.parse_args()

    if not args.key:
        try:
            args.key = str(json.loads((Path(__file__).resolve().parents[1] / "config.json").read_text(encoding="utf-8")).get("auth-key") or "")
        except Exception:
            args.key = ""

    emails = [line.strip() for line in Path(args.emails).read_text(encoding="utf-8").splitlines() if line.strip()]
    out_path = Path(args.out)
    done = set()
    if out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
                if item.get("state") == "ok":
                    done.add(str(item.get("email")).lower())
            except Exception:
                continue
    known_secrets = {}
    if out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
                if item.get("secret"):
                    known_secrets[str(item.get("email")).lower()] = item["secret"]
            except Exception:
                continue
    pending = [email for email in emails if email.lower() not in done]
    if args.limit:
        pending = pending[: args.limit]
    print(f"待注册 {len(pending)} / 共 {len(emails)}")

    from playwright.sync_api import sync_playwright

    successes = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, proxy={"server": args.proxy} if args.proxy else None,
                                    args=["--disable-blink-features=AutomationControlled"])
        try:
            for index, email in enumerate(pending, 1):
                print(f"[{index}/{len(pending)}] {email}", flush=True)
                try:
                    outcome = register_one(browser, email, args.mail_api, print, args.pool, args.key, known_secrets.get(email.lower(), ""), args.import_only)
                except Exception as exc:
                    outcome = {"email": email, "state": "failed", "reason": f"{type(exc).__name__}: {exc}"[:180]}
                if outcome.get("state") == "ok":
                    successes += 1
                    print(f"  成功  {email}|{outcome.get('password') or '-'}|{outcome['secret']}", flush=True)
                else:
                    print(f"  {outcome['state']} @{outcome.get('reason')}", flush=True)
                with out_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(outcome, ensure_ascii=False) + "\n")
                if args.dump:
                    break
        finally:
            browser.close()
    print(f"完成：成功 {successes}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

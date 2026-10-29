#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""批量给 ChatGPT 账号开启（或轮换）TOTP 二次验证。

输入文件每行：email|password            （账号还没有 2FA）
             email|password|旧TOTP密钥   （已有 2FA，需要轮换时）

用法：
    python scripts/bind_totp.py combos.txt
    python scripts/bind_totp.py combos.txt --headless
    python scripts/bind_totp.py combos.txt --proxy http://127.0.0.1:7911
    python scripts/bind_totp.py combos.txt --pool http://127.0.0.1:8000 --key <管理员密码或key>

输出：终端逐行打印 email|password|新TOTP密钥，并追加写入 --out 指定的文件。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.totp_service import code as totp_code, provisioning_uri

ENROLL_URL = "https://chatgpt.com/backend-api/accounts/mfa/enroll"
ACTIVATE_URL = "https://chatgpt.com/backend-api/accounts/mfa/user/activate_enrollment"
SESSION_URL = "https://chatgpt.com/api/auth/session"

EMAIL_SELECTORS = (
    'input[type="email"]',
    'input[name="email"]',
    'input[name="username"]',
    'input#email',
)
PASSWORD_SELECTORS = (
    'input[type="password"]',
    'input[name="password"]',
)
OTP_SELECTORS = (
    'input[autocomplete="one-time-code"]',
    'input[name="code"]',
    'input[inputmode="numeric"]',
)
CONTINUE_TEXTS = ("Continue", "继续", "Next", "下一步", "Log in", "登录", "Sign in")


def log(msg: str) -> None:
    print(msg, flush=True)


def first_visible(page, selectors, timeout_ms: int):
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        for selector in selectors:
            try:
                locator = page.locator(selector)
                if locator.count() and locator.first.is_visible():
                    return locator.first
            except Exception:
                continue
        page.wait_for_timeout(400)
    return None


def click_continue(page) -> bool:
    # 精确匹配，避免把「使用 Google 继续」这种第三方登录按钮当成提交按钮
    for text in CONTINUE_TEXTS:
        try:
            buttons = page.get_by_role("button", name=text, exact=True)
            for index in range(buttons.count()):
                candidate = buttons.nth(index)
                if candidate.is_visible():
                    candidate.click()
                    return True
        except Exception:
            continue
    for selector in ('form button[type="submit"]', 'button[type="submit"]'):
        try:
            button = page.locator(selector)
            if button.count() and button.first.is_visible():
                button.first.click()
                return True
        except Exception:
            continue
    try:
        page.keyboard.press("Enter")
        return True
    except Exception:
        return False


def wait_cloudflare(page, timeout_ms: int = 45000) -> None:
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        title = ""
        try:
            title = page.title()
        except Exception:
            pass
        if "just a moment" not in title.lower() and "attention required" not in title.lower():
            return
        page.wait_for_timeout(1000)


def read_session_token(page) -> str:
    try:
        data = page.evaluate(
            """async (url) => {
                const resp = await fetch(url, {credentials: "include", headers: {accept: "application/json"}});
                if (!resp.ok) return {status: resp.status};
                const json = await resp.json();
                return {status: 200, token: json.accessToken || ""};
            }""",
            SESSION_URL,
        )
    except Exception as exc:
        return ""
    if isinstance(data, dict):
        return str(data.get("token") or "")
    return ""


def enroll_and_activate(page, token: str) -> dict:
    enrolled = page.evaluate(
        """async ({url, token}) => {
            const resp = await fetch(url, {
                method: "POST",
                credentials: "include",
                headers: {
                    "content-type": "application/json",
                    authorization: "Bearer " + token,
                    origin: "https://chatgpt.com",
                    referer: "https://chatgpt.com/",
                },
                body: JSON.stringify({factor_type: "totp"}),
            });
            const text = await resp.text();
            let data = {};
            try { data = JSON.parse(text); } catch (e) { data = {raw: text.slice(0, 300)}; }
            return {status: resp.status, data};
        }""",
        {"url": ENROLL_URL, "token": token},
    )
    if not isinstance(enrolled, dict) or enrolled.get("status") != 200:
        return {"ok": False, "stage": "enroll", "detail": enrolled}
    data = enrolled.get("data") or {}
    secret = str(data.get("secret") or "").replace(" ", "").upper()
    session_id = str(data.get("session_id") or "")
    if not secret or not session_id:
        return {"ok": False, "stage": "enroll", "detail": enrolled}

    activated = page.evaluate(
        """async ({url, token, secret, sessionId, code}) => {
            const resp = await fetch(url, {
                method: "POST",
                credentials: "include",
                headers: {
                    "content-type": "application/json",
                    authorization: "Bearer " + token,
                    origin: "https://chatgpt.com",
                    referer: "https://chatgpt.com/",
                },
                body: JSON.stringify({code, factor_type: "totp", session_id: sessionId}),
            });
            const text = await resp.text();
            let data = {};
            try { data = JSON.parse(text); } catch (e) { data = {raw: text.slice(0, 300)}; }
            return {status: resp.status, data};
        }""",
        {
            "url": ACTIVATE_URL,
            "token": token,
            "secret": secret,
            "sessionId": session_id,
            "code": totp_code(secret),
        },
    )
    if not isinstance(activated, dict) or activated.get("status") != 200:
        return {"ok": False, "stage": "activate", "secret": secret, "detail": activated}
    body = activated.get("data") or {}
    if body.get("success") is not True:
        return {"ok": False, "stage": "activate", "secret": secret, "detail": activated}
    return {"ok": True, "secret": secret}


def fetch_mail_code(mail_api: str, email: str, since_iso: str, timeout: int = 150) -> str:
    import urllib.request

    deadline = time.time() + timeout
    endpoint = mail_api.rstrip("/") + "/api/read_all"
    while time.time() < deadline:
        try:
            req = urllib.request.Request(
                endpoint,
                data=json.dumps({"emails": [email], "full": True}).encode("utf-8"),
                headers={"content-type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=40) as resp:
                payload = json.loads(resp.read().decode("utf-8") or "{}")
        except Exception:
            time.sleep(3)
            continue
        for item in payload.get("results") or []:
            for msg in item.get("messages") or []:
                if since_iso and str(msg.get("time") or "") < since_iso:
                    continue
                codes = [str(c) for c in (msg.get("codes") or []) if len(str(c).strip()) == 6]
                if codes:
                    return codes[0].strip()
        time.sleep(3)
    return ""


def click_password_option(page) -> bool:
    """邮箱验证码页面上的「使用密码继续」入口。"""
    for text in ("使用密码继续", "使用密码登录", "Continue with password", "Use password", "Sign in with password"):
        for role in ("button", "link"):
            try:
                target = page.get_by_role(role, name=text, exact=False)
                if target.count() and target.first.is_visible():
                    target.first.click()
                    return True
            except Exception:
                continue
    return False


def page_text(page) -> str:
    try:
        return page.inner_text("body")[:400]
    except Exception:
        return ""


def is_mfa_page(page) -> bool:
    url = ""
    try:
        url = page.url.lower()
    except Exception:
        pass
    text = page_text(page).lower()
    markers = ("mfa", "身份验证器", "验证器", "authenticator", "two-factor", "two factor")
    return any(marker in url or marker in text for marker in markers)


def bind_one(browser, email: str, password: str, old_secret: str, headless: bool, timeout_ms: int, mail_api: str = "") -> dict:
    from datetime import datetime, timedelta, timezone

    since_iso = (datetime.now(timezone.utc) - timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    context = browser.new_context(
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        viewport={"width": 1280, "height": 900},
    )
    page = context.new_page()
    try:
        page.goto("https://chatgpt.com/auth/login", wait_until="domcontentloaded", timeout=60000)
        wait_cloudflare(page)

        email_input = first_visible(page, EMAIL_SELECTORS, 30000)
        if email_input is None:
            return {"ok": False, "stage": "open_login", "detail": page.url}
        email_input.fill(email)
        click_continue(page)

        # 等真正的下一步（密码框 / 「使用密码继续」/ 验证码框）出现
        first_visible(page, PASSWORD_SELECTORS + OTP_SELECTORS, 20000)
        if "create-account" in page.url or "创建密码" in page_text(page):
            return {"ok": False, "stage": "account_not_found", "detail": "该邮箱在 OpenAI 侧没有账号"}

        # 默认走「邮箱验证码」，有密码时优先切到密码登录
        password_input = first_visible(page, PASSWORD_SELECTORS, 2000)
        if password_input is None and password:
            if click_password_option(page):
                password_input = first_visible(page, PASSWORD_SELECTORS, 15000)
                if password_input is not None and ("create-account" in page.url or "创建密码" in page_text(page)):
                    return {"ok": False, "stage": "account_not_found", "detail": "该邮箱在 OpenAI 侧没有账号"}

        if password_input is not None:
            password_input.fill(password)
            click_continue(page)
            step_deadline = time.time() + 20
            while time.time() < step_deadline:
                page.wait_for_timeout(500)
                if is_mfa_page(page) or "auth.openai.com" not in page.url:
                    break

        wait_cloudflare(page, 30000)

        mfa_input = None
        if is_mfa_page(page):
            mfa_input = first_visible(page, OTP_SELECTORS, 15000)

        if mfa_input is not None:
            if old_secret:
                value = totp_code(old_secret)
                log("  使用已有 TOTP 密钥通过二次验证")
            elif headless:
                return {"ok": False, "stage": "mfa_required", "detail": "账号已开 2FA，需要旧密钥，headless 无法输入"}
            else:
                value = input(f"  [{email}] 账号已开 2FA，请输入当前验证码（或旧密钥）：").strip()
                if not value:
                    return {"ok": False, "stage": "mfa_required", "detail": "用户跳过"}
                if not value.isdigit():
                    old_secret = value.replace(" ", "").upper()
                    value = totp_code(old_secret)
            mfa_input.fill(value)
            click_continue(page)
            page.wait_for_timeout(1500)

        otp_input = None if password_input is not None else first_visible(page, OTP_SELECTORS, 20000)
        if otp_input is not None:
            if mail_api:
                from datetime import datetime, timedelta, timezone as _tz

                try:
                    resend = page.get_by_text("重新发送电子邮件").first
                    if resend.count():
                        resend.click()
                        page.wait_for_timeout(3000)
                except Exception:
                    pass
                log("  等待邮箱验证码…")
                fresh_since = (datetime.now(_tz.utc) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
                value = fetch_mail_code(mail_api, email, fresh_since)
                if not value:
                    return {"ok": False, "stage": "login_otp", "detail": "邮箱里没等到验证码"}
                log(f"  收到验证码 {value}")
            elif headless:
                return {"ok": False, "stage": "login_otp", "detail": "需要邮箱验证码，headless 模式无法输入"}
            else:
                value = input(f"  [{email}] 登录需要邮箱验证码，请输入 6 位数字：").strip()
            if not value:
                return {"ok": False, "stage": "login_otp", "detail": "用户跳过"}
            otp_input.fill(value)
            click_continue(page)
            for _ in range(40):
                page.wait_for_timeout(1000)
                if "chatgpt.com" in page.url and "/auth/" not in page.url:
                    break
            if "chatgpt.com" not in page.url:
                try:
                    page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=45000)
                except Exception:
                    pass

        deadline = time.time() + timeout_ms / 1000
        token = ""
        while time.time() < deadline:
            token = read_session_token(page)
            if token:
                break
            body = page_text(page).lower()
            if any(marker in body for marker in ("密码不正确", "密码错误", "incorrect", "wrong password")):
                return {"ok": False, "stage": "invalid_password", "detail": page_text(page)[:160]}
            url = ""
            try:
                url = page.url
            except Exception:
                pass
            if "chatgpt.com" in url and "/auth/" not in url:
                wait_cloudflare(page, 5000)
            page.wait_for_timeout(1500)

        if not token:
            return {"ok": False, "stage": "no_session_token", "detail": {"url": page.url, "text": page_text(page)[:200]}}

        return enroll_and_activate(page, token)
    finally:
        try:
            context.close()
        except Exception:
            pass


def push_to_pool(pool_url: str, api_key: str, email: str, password: str, secret: str) -> str:
    import urllib.request

    base = pool_url.rstrip("/")

    def call(path: str, payload: dict):
        req = urllib.request.Request(
            base + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"content-type": "application/json", "authorization": f"Bearer {api_key}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")

    listing = urllib.request.Request(
        base + "/api/accounts",
        headers={"authorization": f"Bearer {api_key}"},
    )
    with urllib.request.urlopen(listing, timeout=30) as resp:
        items = json.loads(resp.read().decode("utf-8") or "{}").get("items") or []

    target = next((item for item in items if str(item.get("email") or "").lower() == email.lower()), None)
    if target is None:
        return "号池里没有对应邮箱，跳过回写"
    call(
        "/api/accounts/update",
        {
            "access_token": target.get("access_token"),
            "password": password,
            "totp_secret": secret,
        },
    )
    return "已回写号池"


def parse_combo(line: str):
    parts = [part.strip() for part in line.split("|")]
    if len(parts) < 2 or not parts[0] or "@" not in parts[0]:
        return None
    email, password = parts[0], parts[1]
    old_secret = parts[2].replace(" ", "").upper() if len(parts) > 2 else ""
    return email, password, old_secret


def main() -> int:
    parser = argparse.ArgumentParser(description="批量绑定 ChatGPT TOTP 二次验证")
    parser.add_argument("combos", help="账号文件，每行 email|password[|旧TOTP]")
    parser.add_argument("--proxy", default="http://127.0.0.1:7911", help="出口代理，留空表示直连")
    parser.add_argument("--headless", action="store_true", help="无头模式（遇到验证码/人机校验会失败）")
    parser.add_argument("--out", default="totp_results.txt", help="结果文件 email|password|TOTP密钥")
    parser.add_argument("--only", default="", help="只处理指定邮箱")
    parser.add_argument("--timeout", type=int, default=180, help="单账号登录等待秒数")
    parser.add_argument("--mail-api", default="", help="取码工具地址，如 http://127.0.0.1:19878")
    parser.add_argument("--pool", default="", help="号池地址，如 http://127.0.0.1:8000")
    parser.add_argument("--key", default="", help="号池管理员 key")
    args = parser.parse_args()

    source = Path(args.combos)
    if not source.exists():
        log(f"找不到文件：{source}")
        return 2

    combos = []
    for raw in source.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parsed = parse_combo(line)
        if parsed is None:
            log(f"跳过无法解析的行：{line[:40]}")
            continue
        if args.only and parsed[0].lower() != args.only.lower():
            continue
        combos.append(parsed)

    if not combos:
        log("没有可处理的账号")
        return 2

    log(f"共 {len(combos)} 个账号，出口代理：{args.proxy or '直连'}")

    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        log(f"需要 playwright：{exc}")
        return 2

    proxy_settings = {"server": args.proxy} if args.proxy else None
    results: list[tuple[str, str, str]] = []
    failures: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=args.headless,
            proxy=proxy_settings,
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            for index, (email, password, old_secret) in enumerate(combos, 1):
                log(f"\n[{index}/{len(combos)}] {email}")
                try:
                    outcome = bind_one(browser, email, password, old_secret, args.headless, args.timeout, args.mail_api)
                except Exception as exc:
                    outcome = {"ok": False, "stage": "exception", "detail": f"{type(exc).__name__}: {exc}"}

                if outcome.get("ok"):
                    secret = outcome["secret"]
                    results.append((email, password, secret))
                    log(f"  绑定成功  {email}|{password}|{secret}")
                    log(f"  otpauth: {provisioning_uri(secret, email)}")
                    try:
                        with open(args.out, "a", encoding="utf-8") as fh:
                            fh.write(f"{email}|{password}|{secret}\n")
                    except Exception as exc:
                        log(f"  写结果文件失败：{exc}")
                    if args.pool and args.key:
                        try:
                            log(f"  {push_to_pool(args.pool, args.key, email, password, secret)}")
                        except Exception as exc:
                            log(f"  回写号池失败：{exc}")
                else:
                    failures.append({"email": email, "stage": outcome.get("stage"), "detail": outcome.get("detail")})
                    log(f"  失败 @{outcome.get('stage')}: {json.dumps(outcome.get('detail'), ensure_ascii=False)[:200]}")
                    if outcome.get("secret"):
                        log(f"  警告：激活未确认，候选密钥 {outcome['secret']}（先保留）")
        finally:
            try:
                browser.close()
            except Exception:
                pass

    log(f"\n完成：成功 {len(results)}，失败 {len(failures)}")
    for item in failures:
        log(f"  - {item['email']} @{item['stage']}")
    return 0 if results else 1


if __name__ == "__main__":
    sys.exit(main())

"""批量绑定 ChatGPT 账号的 TOTP 二次验证。

流程：浏览器登录（邮箱验证码或密码，自动从取码工具拿验证码）→ enroll → activate
→ 把密钥写回账号记录。必须有真实浏览器，headless 会被 Cloudflare 挡住。
"""
from __future__ import annotations

import json
import time
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from threading import Lock, Thread

from services.totp_service import code as totp_code

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
        try:
            title = page.title().lower()
        except Exception:
            return
        if "just a moment" not in title and "attention required" not in title and "请稍候" not in title:
            return
        page.wait_for_timeout(1000)


def page_text(page) -> str:
    try:
        return page.inner_text("body")[:400]
    except Exception:
        return ""


def is_mfa_page(page) -> bool:
    try:
        url = page.url.lower()
    except Exception:
        url = ""
    text = page_text(page).lower()
    markers = ("mfa", "身份验证器", "验证器", "authenticator", "two-factor", "two factor")
    return any(marker in url or marker in text for marker in markers)


def click_password_option(page) -> bool:
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
    except Exception:
        return ""
    return str((data or {}).get("token") or "") if isinstance(data, dict) else ""


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
        """async ({url, token, sessionId, code}) => {
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
        {"url": ACTIVATE_URL, "token": token, "sessionId": session_id, "code": totp_code(secret)},
    )
    if not isinstance(activated, dict) or activated.get("status") != 200:
        return {"ok": False, "stage": "activate", "secret": secret, "detail": activated}
    body = activated.get("data") or {}
    if body.get("success") is not True:
        return {"ok": False, "stage": "activate", "secret": secret, "detail": activated}
    return {"ok": True, "secret": secret}


def fetch_mail_code(mail_api: str, email: str, since_iso: str, timeout: int = 150) -> str:
    deadline = time.time() + timeout
    endpoint = mail_api.rstrip("/") + "/api/read_all"
    while time.time() < deadline:
        try:
            req = urllib.request.Request(
                endpoint,
                data=json.dumps({"emails": [email], "full": False}).encode("utf-8"),
                headers={"content-type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
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


def login_and_bind(browser, *, email: str, password: str, existing_totp: str, mail_api: str,
                   timeout_sec: int = 150, log=None) -> dict:
    def note(message: str) -> None:
        if log:
            log(message)

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

        first_visible(page, PASSWORD_SELECTORS + OTP_SELECTORS, 20000)
        if "create-account" in page.url or "创建密码" in page_text(page):
            return {"ok": False, "stage": "account_not_found", "detail": "该邮箱在 OpenAI 侧没有账号"}

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

        mfa_input = first_visible(page, OTP_SELECTORS, 15000) if is_mfa_page(page) else None
        if mfa_input is not None:
            if not existing_totp:
                return {"ok": False, "stage": "mfa_required", "detail": "账号已开 2FA，但没有存入旧密钥"}
            note("用旧密钥通过二次验证")
            mfa_input.fill(totp_code(existing_totp))
            click_continue(page)
            page.wait_for_timeout(1500)

        otp_input = None if password_input is not None else first_visible(page, OTP_SELECTORS, 20000)
        if otp_input is not None:
            if not mail_api:
                return {"ok": False, "stage": "login_otp", "detail": "需要邮箱验证码，但没有配置取码工具地址"}
            try:
                resend = page.get_by_text("重新发送电子邮件").first
                if resend.count():
                    resend.click()
                    page.wait_for_timeout(3000)
            except Exception:
                pass
            note("等待邮箱验证码")
            fresh_since = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
            value = fetch_mail_code(mail_api, email, fresh_since)
            if not value:
                return {"ok": False, "stage": "login_otp", "detail": "邮箱里没等到验证码"}
            note(f"收到验证码 {value}")
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

        deadline = time.time() + timeout_sec
        token = ""
        while time.time() < deadline:
            token = read_session_token(page)
            if token:
                break
            body = page_text(page).lower()
            if any(marker in body for marker in ("密码不正确", "密码错误", "incorrect", "wrong password")):
                return {"ok": False, "stage": "invalid_password", "detail": page_text(page)[:160]}
            page.wait_for_timeout(1500)

        if not token:
            return {"ok": False, "stage": "no_session_token", "detail": {"url": page.url, "text": page_text(page)[:160]}}

        return enroll_and_activate(page, token)
    finally:
        try:
            context.close()
        except Exception:
            pass


class TotpBindService:
    def __init__(self) -> None:
        self._jobs: dict[str, dict] = {}
        self._lock = Lock()

    def start(self, access_tokens: list[str], mail_api: str = "", headless: bool = False) -> str:
        job_id = uuid.uuid4().hex[:12]
        job = {
            "job_id": job_id,
            "total": len(access_tokens),
            "processed": 0,
            "done": False,
            "error": "",
            "results": [],
            "started_at": time.time(),
        }
        with self._lock:
            self._jobs[job_id] = job
        Thread(target=self._run, args=(job_id, list(access_tokens), mail_api, headless), daemon=True).start()
        return job_id

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def _update(self, job_id: str, **fields) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.update(fields)

    def _append_result(self, job_id: str, result: dict) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job["results"].append(result)
                job["processed"] = len(job["results"])

    @staticmethod
    def _browser_proxy(url: str) -> str:
        """Playwright 只认 http(s)/socks5；socks5h 会造成 ERR_NO_SUPPORTED_PROXIES。"""
        url = str(url or "").strip()
        if url.startswith("socks5h://"):
            url = "socks5://" + url[len("socks5h://"):]
        return url

    def _proxy_candidates(self, preferred: str) -> list[str]:
        candidates: list[str] = []
        seen: set[str] = set()

        def add(url: str) -> None:
            url = self._browser_proxy(url)
            if not url or url in seen:
                return
            if not url.startswith(("http://", "https://", "socks5://")):
                return
            seen.add(url)
            candidates.append(url)

        add(preferred)
        try:
            from services.egress_service import egress_manager

            snapshot = egress_manager.snapshot()
            endpoints = snapshot.get("endpoints") if isinstance(snapshot, dict) else snapshot
            healthy = [item for item in endpoints or [] if item.get("healthy") is True]
            others = [item for item in endpoints or [] if item.get("healthy") is not True]
            for item in healthy + others:
                add(str(item.get("url") or ""))
        except Exception:
            pass
        return candidates

    def _run(self, job_id: str, access_tokens: list[str], mail_api: str, headless: bool) -> None:
        from services.account_service import account_service

        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            self._update(job_id, done=True, error=f"当前环境没有 Playwright：{exc}")
            return

        from services.proxy_service import proxy_settings

        try:
            with sync_playwright() as pw:
                browser = None
                try:
                    for access_token in access_tokens:
                        account = account_service.get_account(access_token) or {}
                        email = str(account.get("email") or "").strip()
                        result = {"token": access_token, "email": email, "status": "失败", "secret": "", "error": ""}
                        if not email:
                            result["error"] = "账号缺少邮箱"
                            self._append_result(job_id, result)
                            continue

                        proxy_url = proxy_settings.get_profile(account=account, upstream=True).proxy_url
                        outcome = {"ok": False, "stage": "exception", "detail": "没有可用的出口代理"}
                        for candidate in self._proxy_candidates(proxy_url) or [""]:
                            try:
                                if browser is not None:
                                    browser.close()
                                    browser = None
                                browser = pw.chromium.launch(
                                    headless=headless,
                                    proxy={"server": candidate} if candidate else None,
                                    args=["--disable-blink-features=AutomationControlled"],
                                )
                                outcome = login_and_bind(
                                    browser,
                                    email=email,
                                    password=str(account.get("password") or "").strip(),
                                    existing_totp=str(account.get("totp_secret") or "").strip(),
                                    mail_api=mail_api,
                                )
                                break
                            except Exception as exc:
                                text = str(exc)
                                outcome = {"ok": False, "stage": "exception", "detail": f"{type(exc).__name__}: {text}"[:300]}
                                if "PROXY" not in text.upper():
                                    break

                        if outcome.get("ok"):
                            secret = str(outcome.get("secret") or "")
                            account_service.update_account(access_token, {"totp_secret": secret}, quiet=True)
                            result.update(status="成功", secret=secret)
                        else:
                            detail = outcome.get("detail")
                            result["error"] = f"{outcome.get('stage')}: {json.dumps(detail, ensure_ascii=False) if not isinstance(detail, str) else detail}"[:300]
                        self._append_result(job_id, result)
                finally:
                    if browser is not None:
                        try:
                            browser.close()
                        except Exception:
                            pass
            self._update(job_id, done=True)
        except Exception as exc:
            self._update(job_id, done=True, error=f"{type(exc).__name__}: {exc}")


totp_bind_service = TotpBindService()

# 注册模块（可选）

本项目本身只做**号池管理 + 反代**，不内置注册。注册由两个独立开源项目完成，二选一即可，产出的凭据通过控制台的「注册」页或导入接口进入号池。

两条路线都已在真实环境验证过，差别在于**浏览器内核**与**运行位置**：

| | 方案 A：grok-register | 方案 B：grok-register-panel |
|---|---|---|
| 上游项目 | [AaronL725/grok-register](https://github.com/AaronL725/grok-register)（MIT） | [lij768423-svg/grok-register-panel](https://github.com/lij768423-svg/grok-register-panel)（MIT，基于前者） |
| 浏览器 | Chromium（DrissionPage） | Camoufox（Firefox 内核，C++ 层指纹伪装） |
| 运行位置 | 本机 / 任意桌面系统 | Linux VPS（无头） |
| 交互 | WebUI + CLI | 无 web 界面时走命令行，批量编排更成熟 |
| 适合 | 少量精跑、调试 | 大批量、长期挂机 |

> 两个项目都需要**你自己的**邮箱服务、代理出口和域名。仓库里不提供任何可用的邮箱、代理或域名。

---

## 一、共同前提

### 1. 邮箱（必须有，能收验证码）

x.ai 只给「看起来正常」的域名投递验证码。可选来源：

- **自建 Cloudflare 收信**（推荐）：域名托管在 Cloudflare → 开启 Email Routing catch-all → 指向一个同时实现 `email()` 与 `fetch()` 的 Worker（收信存 KV，HTTP 读取）
  - 服务器需要两个接口：`POST /api/new_address`（返回 `{address, jwt}`）、`GET /api/mails`（Bearer 传地址）
- **第三方临时邮箱**：DuckMail / YYDS / CloudMail / Mail.tm 等。公共域名经常被 x.ai 拒收，只适合临时试用
- **Outlook 邮箱池**：格式 `email----password----client_id----refresh_token`，注意字段顺序，第 3 段是 client_id 而非 token

⚠️ 域名寿命是硬约束：一个域名大约能注册 **2000 个号**，短时间猛刷会提前报废；报废后页面直接提示 `Email sign-up isn't available right now`。**这个报错换 IP 没用，只能换域名。**

### 2. 出口代理

- 浏览器会话必须走**粘性出口**：一个账号从打开页面到提交完成，出口 IP 不能变。轮询/负载均衡型代理会让页面渲染残缺（`inputs=none; buttons=none`）
- 住宅/家宽出口最稳；机房 IP 通常能打开页面但过不了 Turnstile
- 单 IP 注册配额大约 **1–2 个号**，之后就需要更换出口
- 批量场景可以用代理池，但要求「按账号粘性」而不是「按连接轮换」

### 3. 账号类型

注册产出的是 **web（SSO）号**；带 CPA/OIDC 导出时同时得到 **build 号**（可直连 `cli-chat-proxy`）。

- `console` 类型不在注册范围内：console.x.ai 需要付费额度，免费号 `/v1/usage` 三种额度均为 0，调用返回 `429 resource-exhausted`

---

## 二、方案 A：grok-register（Chromium）

### 安装

```bash
git clone https://github.com/AaronL725/grok-register.git
cd grok-register
python -m venv .venv
# Windows: .venv\Scripts\activate    Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
pip install "fastapi>=0.115,<0.116" "uvicorn>=0.34,<0.35"   # resolver 常跳过
python -m web.server        # 默认 http://127.0.0.1:8092
```

### 关键配置（`config.json`）

| 键 | 说明 |
|---|---|
| `email_provider` | `cloudflare` / `duckmail` / `yyds` / `cloudmail` / `outlook` |
| `cloudflare_api_base` | 你的取信服务地址，如 `https://mail.example.com` |
| `cloudflare_path_accounts` / `cloudflare_path_messages` | 默认 `/api/new_address` / `/api/mails` |
| `defaultDomains` | 收信域名，多个用逗号分隔，按顺序轮换 |
| `proxy_mode` | `single` 或 `pool` |
| `proxy` / `proxy_pool_file` | 单条代理 / 代理池文件（一行一个，支持 `ip:port`、`http://`、`socks5://`） |
| `register_count` / `multi_thread_workers` | 目标数量 / 并发（建议 2–4） |
| `cpa_headless` | `true` 时授权浏览器不弹窗 |
| `cpa_force_standalone` | `false` 复用注册浏览器会话，成功率更高 |

### 运行

```bash
# WebUI
python -m web.server     # 打开 http://127.0.0.1:8092 点击开始
# CLI
python grok_register_ttk.py cli
```

产物：`accounts_*.txt`（`email----password----sso`）与 `cpa_auths/xai-<email>.json`（build 凭据）。

### 接入控制台

控制台 → **注册** → 「注册服务地址」填 `http://127.0.0.1:8092` → 保存并连接。之后在网页里改域名/代理/并发、启动任务、看实时日志。

---

## 三、方案 B：grok-register-panel（Camoufox，VPS 无头）

### 安装（Debian/Ubuntu）

```bash
sudo apt install -y python3-venv xvfb
git clone https://github.com/lij768423-svg/grok-register-panel.git
cd grok-register-panel
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m camoufox fetch      # 下载 Firefox 内核
```

### 关键配置（`config.json`）

| 键 | 说明 |
|---|---|
| `email_provider` | 同上；`cloudflare` 模式需填 `cloudflare_api_base` 与三个 path |
| `defaultDomains` | 收信域名 |
| `proxy` | **粘性**出口，如 `socks5://127.0.0.1:1080` |
| `cpa_auto_add` | `true` 时注册后自动导 CPA |
| `cpa_token_mode` | `device_protocol`（纯 HTTP 设备码，不需要浏览器） |
| `register_workers` / `account_interval` | 并发 / 账号间隔（默认 `60-120` 秒，**不要关**） |

### 运行

```bash
# 必须带 GROK_HEADLESS=1，否则 Camoufox 因无 DISPLAY 直接退出
GROK_HEADLESS=1 .venv/bin/python -u run_batch_headless.py <数量> <并发>

# 跑到达标为止（推荐批量）
ORCH_ADD_COUNT=100 GROK_HEADLESS=1 .venv/bin/python -u run_until_100.py
```

`run_until_100.py` 会一轮轮地启动子批次，累计到目标数量才停；命中风控会暂停并重启批次。

### 已知坑

- **代理必须粘性**：用轮询池时页面会 52 秒才打开或直接空壳
- **语言**：Camoufox 会按出口 IP 推导页面语言（波兰语/法语等），必须在 `browser_session.py` 里强制 `locale="en-US"`，否则按钮文案匹配失败
- **超时**：慢代理下把页面壳/邮箱框等待从 8s 调到 40s、提交从 10s 调到 60s
- **SSO→auth 的 consent 页**会返回 `400 Invalid Server Action request`，脚本需要通过解析页面 JS chunk 里的 `Next-Action` 逐个重试才能拿到 authorization code

---

## 四、产出入池

| 类型 | 导入方式 | 说明 |
|---|---|---|
| build（CPA） | `POST /api/admin/v1/accounts/import` `provider=grok_build` | JSON 数组，含 `access_token` / `refresh_token` / `client_id` |
| web（SSO） | 同上，`provider=grok_web` | 纯文本一行一个 SSO，或 JSON 含 `sso_token` |

控制台侧也可以直接用 **注册** 页跑完后在 **账号** 页导入；或让注册脚本自己调用号池的导入接口。

---

## 五、合规

注册脚本、代理、邮箱均为你自己的资源与账号。请遵守目标服务条款与当地法律。

# 部署架构

**一个项目，三个部分。** 不是「一个项目去连接另一个项目」。

```
chatgpt-pool-ng/
├── api/          Python 网关（chatgpt2api 基座）
├── services/     Python 服务层：号池 / 出口治理 / 可观测 / 控制台代理
├── web/          Next.js 控制台前端
├── upstreams/
│   ├── grok2api/     Go 号池网关（源码）
│   └── minimax2api/  Go 号池网关（源码）
└── deploy/
```

## 端口

| 服务 | 端口 | 说明 |
|---|---|---|
| chatgpt-pool-ng | 8000（容器内 80） | OpenAI 兼容 API + 控制台 |
| grok2api | 8002 | Grok 号池网关（Go） |
| minimax2api | 8096 | MiniMax 号池网关（Go） |

控制台通过 `http://127.0.0.1:8000` 访问，页面上管理另外两个网关。

## 本机裸跑（Windows）

```powershell
# 编译两个 Go 网关（Go 1.24+，缓存指到 D 盘避免 C 盘不够）
$env:GOCACHE='D:\xi\gocache'; $env:GOTMPDIR='D:\xi\gotmp'; $env:GOPATH='D:\xi\gopath'
$env:GOPROXY='https://goproxy.cn,direct'; $env:GOSUMDB='off'
cd upstreams\grok2api\backend;    go build -o bin\grok2api.exe ./cmd/grok2api
cd ..\..\minimax2api\backend;     go build -o bin\minimax2api.exe ./cmd/minimax2api

# 起全部服务（分离式进程，不受后台任务 1 小时上限影响）
powershell -ExecutionPolicy Bypass -File deploy\start_services.ps1
powershell -ExecutionPolicy Bypass -File deploy\start_services.ps1 -Stop   # 全停
```

运行时数据不进仓库，各自放在：

- `upstreams/grok2api/data/backend.db`（SQLite，账号库）
- `upstreams/minimax2api/data/app.json`（账号库）
- `data/grok_console.json` / `data/minimax_console.json`（控制台连接配置，含口令）

## Docker

```bash
docker compose up -d --build          # 一个项目，三个服务
docker compose -f docker-compose.local.yml up -d --build   # 本地开发变体
```

容器间用**服务名**互相访问，所以控制台的上游地址由环境变量注入：

| 变量 | 作用 |
|---|---|
| `GROK2API_BASE_URL` | 覆盖 `data/grok_console.json` 里的 grok2api 地址 |
| `MINIMAX_BASE_URL` | 覆盖 `data/minimax_console.json` 里的 minimax2api 地址 |
| `MINIMAX_ADMIN_USER` / `MINIMAX_ADMIN_PASSWORD` | 覆盖 minimax2api 管理员口令 |

裸跑时这两个变量不设，读本地配置文件即可。

## 为什么不用 SSH 隧道

两个 Go 网关跑在同一台机器上时，直接走回环即可。
实测去掉隧道后的响应时间：

| 端点 | 跨机 SSH 隧道 | 本机直连 |
|---|---|---|
| grok/dashboard | 1119 ms | 92 ms |
| grok/accounts | 679 ms | 27 ms |
| minimax/dashboard | 555 ms | 44 ms |
| minimax/accounts | 974 ms | 22 ms |

`services/ssh_tunnel.py` 仍然保留，作为「网关部署在另一台机器」时的兜底路径
（默认关闭，可在控制台的「连接设置」页开启）。

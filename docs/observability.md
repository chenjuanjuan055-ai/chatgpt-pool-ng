# 可观测与告警

## 请求指标

每次 `/v1/*` 调用（成功或失败）都会进入进程内环形缓冲（默认 4000 条），来自 `LoggedCall.log`，字段：端点、模型、状态、耗时、错误原文、错误分类、账号邮箱。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/metrics/summary?window_seconds=3600` | 总量/失败数/成功率/RPM/P50/P95、按模型、按错误分类、按端点、最近失败样本 |
| GET | `/api/metrics/recent?limit=100` | 最近调用明细（新→旧） |
| GET | `/healthz` | 免鉴权探活：账号健康计数、出口池状态、近 5 分钟指标摘要 |

## 错误分类

| 分类 | 触发关键词（示例） | 含义 |
|---|---|---|
| `risk_control` | request illegal / 11140 / unusual activity / WAF / cloudflare | 上游风控拦截 |
| `rate_limit` | 429 / usage exceeds / 6004 / 限流 | 频率或额度限制 |
| `auth` | 401/403 / invalidated / session has ended / refresh_token | 凭证失效 |
| `network` | timeout / TLS / proxy / curl / DNS | 网络或代理故障 |
| `upstream` | 5xx | 上游服务端错误 |
| `client` | 400/422 / invalid request / 参数 | 请求不合法 |

分类只用于聚合和告警提示，不改写返回给客户端的原始错误。

## 告警

`config.json -> alerts`：

```jsonc
{
  "enabled": true,
  "webhook_url": "https://example.com/hook",   // 通用 JSON POST
  "consecutive_failures": 5,                   // 连续失败 N 次立即告警
  "window_seconds": 300,
  "min_requests": 20,
  "max_failure_rate": 0.5,                     // 窗口内失败率 ≥50% 也告警
  "cooldown_sec": 300,                         // 两次告警最小间隔
  "qq": { "url": "", "token": "", "umo": "" }   // 可选：AstrBot 兼容 HTTP 桥
}
```

- 告警在写入指标时判定，派发走后台线程，不阻塞请求。
- `POST /api/alerts/settings` 保存配置；`GET /api/alerts/settings` 读取；`POST /api/alerts/test` 向已配置通道发一条测试消息（返回各通道是否成功）。

## 日志轮转

`data/logs.jsonl` 超过 20MB 自动轮转为 `logs.jsonl.1`（只保留一份历史），避免长期运行撑爆磁盘。

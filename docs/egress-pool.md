# 出口池（Egress Pool）

多个出站代理（通常是本机 mihomo/xray 的 HTTP 入口）组成一个池，上游请求按健康度自动选路，坏出口自动摘除并在冷却后自动恢复。

## 为什么需要

单出口部署时，代理一挂，整池报 `no_available_account` / TLS 连接失败，看起来像账号全废。出口池把这类故障收敛到"换一条线继续跑"，并在面板上暴露每条线的健康状态。

## 配置

```jsonc
// config.json -> proxy_runtime
{
  "enabled": true,              // 代理运行时总开关
  "egress_mode": "single_proxy",
  "egress_pool": {
    "enabled": true,
    "auto_failover": true,      // 传输失败自动换出口
    "max_attempts": 3,          // 单次请求最多尝试的出口数
    "probe_url": "https://chatgpt.com/backend-api/me",
    "probe_interval": 120,      // 后台探活间隔（秒，0=关闭）
    "timeout_sec": 8,
    "failure_threshold": 2,     // 连续失败几次进入冷却
    "cooldown_sec": 300,
    "endpoints": [
      { "url": "http://127.0.0.1:7932", "label": "us-a", "enabled": true, "weight": 1.0 },
      { "url": "http://127.0.0.1:7933", "label": "us-b", "enabled": true, "weight": 1.0 }
    ]
  }
}
```

开启出口池后 `egress_mode` 不再限制选路（`single_proxy` 下也会优先走池）。

## 选路规则

1. 健康（探活成功或有成功请求）的出口优先，按 EWMA 延迟升序；
2. 未知状态的出口次之；
3. 失败过但还没到冷却阈值的出口最后；
4. 冷却中的出口跳过；若全部在冷却，取最早到期的一条做探测性重试。

## 失败与恢复

- 只有**传输层错误**（连接失败 / TLS / 超时 / 代理错误）计入失败；HTTP 4xx/5xx 不算。
- 连续失败达到 `failure_threshold` 后进入 `cooldown_sec` 冷却；冷却期间不参与选路。
- 后台探活（`probe_interval`）成功一次即恢复；探活 `200/401` 视为出口可用（401 = 链路通、只是没带凭证）。
- 请求成功会清零失败计数并更新延迟。

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/proxy/egress` | 当前配置 + 每条出口的健康快照 |
| POST | `/api/proxy/egress/probe` | 立即探活全部出口 |
| POST | `/api/proxy/egress/settings` | 保存出口池配置（body 即 `egress_pool` 对象） |

三个接口都需要管理员鉴权（与其它 `/api/proxy/*` 一致）。

## 与旧配置的关系

- 账号级 `proxy` 字段优先级最高，不受出口池影响；
- 未启用出口池时行为与旧版一致（`single_proxy` / `direct`）。

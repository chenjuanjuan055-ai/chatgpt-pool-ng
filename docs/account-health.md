# 账号健康与导入校验

## 分类

| 状态码 | 含义 | 是否死号 |
|---|---|---|
| `ok` | 有 RT，AT 有效期充足 | 否 |
| `expiring` | 有 RT，但 AT 已过期或 24h 内过期（可自动续期） | 否 |
| `at_only` | 没有 refresh_token（AT 过期后不可续期） | 否（但高危） |
| `at_expired_no_rt` | 没有 RT 且 AT 已过期 | **是** |
| `refresh_invalid` | RT 刷新报 invalidated / 401 / revoked | **是** |
| `missing_at` | 连 access_token 都没有 | **是** |
| `codex_managed` | Codex/CPA 托管账号（凭据由 CPA 池提供） | 否 |

判定依据只读账号本地字段：`access_token`（解析 JWT `exp`）、`refresh_token` 是否存在、
`last_token_refresh_error` / `last_refresh_error`、`source_type` / `export_type`。

## 导入校验

`POST /api/accounts`：

- `accounts` 数组逐条校验；**缺少 refresh_token 的 web 类型的号会被拒绝**，放在返回的
  `rejected[]` 里并给出原因，不会入库。
- `tokens`（裸 AT 列表）默认**拒绝**，返回 400 并说明原因；如确需导入，显式传
  `"allow_access_token_only": true`。
- Codex/CPA 号（`source_type=codex`）豁免 RT 要求。

## 健康汇总与死号清理

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/accounts/health-summary` | 总数/健康/死号/仅 AT 数量、按状态、按账号年龄分桶、死号样本（令牌脱敏） |
| POST | `/api/accounts/cleanup-dead` | `{apply:false}` 预览；`{apply:true}` 真删。默认删三类死号，传 `include_at_only:true` 可把“仅 AT”的高危号一起清掉 |

## 背景

2026-10-18 导入的一批 19 个号只带 access_token、没有 refresh_token，AT 过期后整批失效，
而旧版导入不会提示。现在同样的导入会被直接拦下并说明原因。

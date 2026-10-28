# Changelog

本项目（ChatGPT 号池 / chatgpt-pool-ng）在 [basketikun/chatgpt2api](https://github.com/basketikun/chatgpt2api) v1.8.0（MIT）基础上二次开发，以下为本项目自己的版本记录。

## 1.9.0 - 2026-10-28

+ [新增] 出口池：多出口健康探测（200/401 视为可达）、EWMA 延迟选路、传输失败自动切换、连续失败冷却后自动恢复。
+ [新增] 出口池配置 `proxy_runtime.egress_pool`，支持 `probe_interval`、`failure_threshold`、`cooldown_sec`、`max_attempts`。
+ [新增] 出口池接口：`GET /api/proxy/egress`、`POST /api/proxy/egress/probe`、`POST /api/proxy/egress/settings`。
+ [新增] 代理节点批量导入（每行 `url[,备注[,权重[,分组]]]`）、分组列表、切换生效分组；出口池按分组选路与探活。
+ [新增] 账号健康分类：正常 / 即将过期 / 仅 AT / AT 过期且无 RT / RT 失效 / 缺 AT / Codex 托管。
+ [新增] 账号健康汇总 `GET /api/accounts/health-summary`：状态分布、账号年龄分桶、死号样本（令牌脱敏）。
+ [新增] 死号清理 `POST /api/accounts/cleanup-dead`：预览 / 执行，可选清理“仅 AT”的高危号。
+ [新增] 导入校验：缺少 refresh_token 的网页号直接拒绝并说明原因；裸 access_token 导入默认禁用，Codex/CPA 号豁免。
+ [新增] 请求指标：成功率、RPM、P50/P95、按模型、按端点、按错误分类、最近失败样本。
+ [新增] 错误分类：风控 / 限流 / 凭证 / 网络 / 上游 / 请求参数 / 号池无可用账号，附中文处置建议。
+ [新增] 告警：连续失败与失败率双阈值，支持通用 Webhook 与 AstrBot QQ 通道，冷却去重、异步派发，提供测试接口。
+ [新增] 按小时仪表盘：聚合结果落盘保留 7 天，重启不丢；`GET /api/dashboard?hours=24`。
+ [新增] 探活接口 `GET /healthz`：账号健康计数、出口池状态、近 5 分钟指标摘要。
+ [新增] 日志自动轮转：`data/logs.jsonl` 超过 20MB 转为 `.1`，避免长期运行撑爆磁盘。
+ [新增] 模型目录 `GET /api/model-catalog`：统一对话/图片清单、默认模型、能力标记、revision。
+ [新增] 图片模型族扩展：`gpt-image-2.5`、`gpt-image-2.5-flare`、`gpt-image-2.5-sunburst`；`gpt-image-2` 上游 slug 可配置（默认 `gpt-5-3`）。
+ [新增] 账号分组：分组 CRUD、账号批量归组、密钥绑定分组后只能调度同组账号（管理员不受限）。
+ [新增] 提示词库：提示词增删查接口，便于生图工作流复用。
+ [新增] 管理界面：左侧栏导航、深色侧边栏、靛蓝主色，新增「监控」页（账号健康 / 出口池 / 指标 / 按小时仪表盘）。
+ [调整] 版本检查与更新日志改为读取本项目仓库，移除原项目跳转与品牌信息。
+ [调整] 生图页改为单列布局（去掉重复的历史侧栏，历史记录收进弹窗），主视觉改为中文标题与新配色，发送/新建按钮统一为主色。
+ [调整] 生图设置浮层重新配色：选中态改为主色描边+浅主色底，尺寸/数量控件统一卡片与边框样式。
+ [调整] 生图设置浮层改为横向布局：宽度增至 720px，宽高比/数量改 6 列、按钮高度收紧，并限制最大高度可滚动，避免小屏溢出。
+ [优化] 前端构建产物统一输出到 `web_dist`，一键部署脚本 `deploy/install.sh` 与 systemd 单元随包提供。
+ [优化] 新增 9 套单元测试（出口池、账号健康、指标告警、模型目录、账号分组、小时聚合、节点解析、提示词、配置）。
+ [文档] 新增 `docs/egress-pool.md`、`docs/account-health.md`、`docs/observability.md`，重写 README 与 NOTICE。

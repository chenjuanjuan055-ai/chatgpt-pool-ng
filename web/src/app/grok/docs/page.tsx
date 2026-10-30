"use client";

import { Card, CardContent } from "@/components/ui/card";
import { useAuthGuard } from "@/lib/use-auth-guard";

const GROUPS = [
  {
    title: "Chat",
    items: [
      { method: "POST", path: "/v1/chat/completions", desc: "OpenAI 兼容对话补全，支持 stream" },
      { method: "GET", path: "/v1/models", desc: "可用模型列表" },
    ],
  },
  {
    title: "Image",
    items: [
      { method: "POST", path: "/v1/images/generations", desc: "文生图" },
      { method: "POST", path: "/v1/images/edits", desc: "图生图 / 编辑" },
    ],
  },
  {
    title: "Video",
    items: [
      { method: "POST", path: "/v1/videos/generations", desc: "视频生成" },
      { method: "GET", path: "/v1/videos/{id}", desc: "查询视频任务" },
    ],
  },
  {
    title: "管理（本项目）",
    items: [
      { method: "GET", path: "/api/grok/dashboard", desc: "仪表盘数据（range=24h|7d|30d|90d）" },
      { method: "GET", path: "/api/grok/accounts", desc: "账号分页 / 搜索 / 来源筛选" },
      { method: "POST", path: "/api/grok/accounts/ops", desc: "批量操作：refresh_tokens / cleanup / detect / convert_to_build" },
      { method: "POST", path: "/api/grok/accounts/import", desc: "导入账号（multipart）" },
      { method: "GET", path: "/api/grok/accounts/export", desc: "导出账号 JSON" },
      { method: "GET|POST|DELETE", path: "/api/grok/keys", desc: "客户端密钥管理" },
      { method: "GET", path: "/api/grok/model-routes", desc: "模型路由表" },
      { method: "POST", path: "/api/grok/accounts/device/start", desc: "设备码登录" },
    ],
  },
];

export default function GrokDocsPage() {
  const { isCheckingAuth } = useAuthGuard(["admin"]);
  if (isCheckingAuth) return null;
  return (
    <div className="space-y-4 pt-6">
      <div>
        <p className="text-[11px] font-medium uppercase tracking-[0.28em] text-stone-400">Grok</p>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight text-stone-900">文档</h1>
        <p className="mt-1 text-sm text-stone-500">Grok 反代对外接口一览。</p>
      </div>

      <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
        <CardContent className="space-y-3 p-5">
          <h2 className="text-sm font-semibold text-stone-800">注册（外部服务，二选一）</h2>
          <p className="text-xs text-stone-500">
            本项目不内置注册。两个开源项目产出凭据后，导入号池或在「注册」页直接驱动。
          </p>
          <div className="grid gap-3 md:grid-cols-2">
            <div className="rounded-xl bg-stone-50 p-3 text-xs">
              <p className="font-medium text-stone-700">A · grok-register（Chromium）</p>
              <p className="mt-1 text-stone-500">
                本机运行，WebUI 在 <span className="font-mono">127.0.0.1:8092</span>；「注册」页填该地址即可驱动。
                适合少量精跑与调试。
              </p>
            </div>
            <div className="rounded-xl bg-stone-50 p-3 text-xs">
              <p className="font-medium text-stone-700">B · grok-register-panel（Camoufox）</p>
              <p className="mt-1 text-stone-500">
                Linux VPS 无头运行，<span className="font-mono">GROK_HEADLESS=1 run_batch_headless.py</span>；
                自带编排与风控暂停，适合大批量。
              </p>
            </div>
          </div>
          <ul className="list-disc space-y-1 pl-4 text-xs text-stone-500">
            <li>邮箱：自建 Cloudflare 收信或第三方临时邮箱，需能接收验证码</li>
            <li>代理：必须粘性出口（一账号一 IP），单 IP 约 1–2 个号</li>
            <li>域名：单域名约 2000 个号上限，报废后只能换域名</li>
            <li>产物：web 号（SSO）与 build 号（CPA），均可导入本控制台</li>
          </ul>
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        {GROUPS.map((group) => (
          <Card key={group.title} className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
            <CardContent className="p-5">
              <h2 className="mb-3 text-sm font-semibold text-stone-800">{group.title}</h2>
              <div className="space-y-2">
                {group.items.map((item) => (
                  <div key={item.path} className="flex items-start gap-2 text-xs">
                    <span className="rounded bg-indigo-50 px-1.5 py-0.5 font-mono text-indigo-600">{item.method}</span>
                    <div className="min-w-0">
                      <p className="truncate font-mono text-stone-700">{item.path}</p>
                      <p className="text-stone-400">{item.desc}</p>
                    </div>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}

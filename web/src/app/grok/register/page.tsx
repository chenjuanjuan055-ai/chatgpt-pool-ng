"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { CircleStop, LoaderCircle, Play, RefreshCw, Save, Server, UserPlus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  fetchRegisterLogs,
  fetchRegisterPoolContent,
  fetchRegisterSettings,
  fetchRegisterStatus,
  saveRegisterPoolContent,
  saveRegisterSettings,
  startRegister,
  stopRegister,
  testRegisterPool,
  updateRegisterConfig,
} from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";

type FieldType = "text" | "password" | "number" | "switch" | "textarea" | "select";
type Field = { key: string; label: string; type?: FieldType; hint?: string; options?: string[] };
type Group = { id: string; label: string; fields: Field[] };

const GROUPS: Group[] = [
  {
    id: "task",
    label: "任务",
    fields: [
      { key: "register_count", label: "目标数量", type: "number" },
      { key: "multi_thread_workers", label: "并发数", type: "number" },
      { key: "multi_thread_enabled", label: "启用并发", type: "switch" },
      { key: "enable_nsfw", label: "注册后开启 NSFW", type: "switch" },
      { key: "user_agent", label: "浏览器 UA", hint: "留空用默认 Chrome UA" },
    ],
  },
  {
    id: "mail",
    label: "邮箱",
    fields: [
      {
        key: "email_provider",
        label: "邮箱服务",
        type: "select",
        options: ["cloudflare", "duckmail", "yyds", "cloudmail", "outlook", "mailtm"],
      },
      { key: "defaultDomains", label: "收信域名", type: "textarea", hint: "多个用逗号或换行分隔，按顺序轮换" },
      { key: "cloudflare_api_base", label: "Cloudflare 取信地址", hint: "例如 https://mail.example.com" },
      { key: "cloudflare_api_key", label: "Cloudflare API Key", type: "password" },
      { key: "cloudflare_auth_mode", label: "鉴权方式", type: "select", options: ["none", "bearer", "x-api-key", "x-admin-auth", "query-key"] },
      { key: "cloudflare_path_accounts", label: "建箱路径" },
      { key: "cloudflare_path_messages", label: "取信路径" },
      { key: "duckmail_api_key", label: "DuckMail Key", type: "password" },
      { key: "yyds_api_key", label: "YYDS API Key", type: "password" },
      { key: "yyds_jwt", label: "YYDS JWT", type: "password" },
      { key: "outlook_accounts_file", label: "Outlook 卡密文件", hint: "email----pwd----client_id----refresh_token" },
      { key: "cloudmail_api_base", label: "CloudMail 地址" },
      { key: "cloudmail_public_token", label: "CloudMail Token", type: "password" },
      { key: "cloudmail_domains", label: "CloudMail 域名" },
      { key: "mailtm_api_base", label: "Mail.tm 地址" },
      { key: "mailtm_domain", label: "Mail.tm 指定域名" },
    ],
  },
  {
    id: "proxy",
    label: "代理",
    fields: [
      { key: "proxy_mode", label: "代理模式", type: "select", options: ["auto", "direct", "single", "pool"] },
      { key: "proxy", label: "单条代理", hint: "proxy_mode=single 时使用，如 http://ip:port" },
      { key: "proxy_fallback", label: "回退策略" },
      { key: "proxy_pool_file", label: "代理池文件", hint: "一行一个：ip:port / http:// / socks5://" },
      { key: "proxy_pool_subscription_url", label: "代理池订阅 URL" },
      { key: "proxy_pool_subscription_proxy", label: "拉订阅用的代理" },
      { key: "proxy_pool_endpoint_mode", label: "端点模式", type: "select", options: ["auto", "fixed", "rotating"] },
      { key: "proxy_pool_max_concurrent_per_node", label: "单节点并发", type: "number" },
      { key: "proxy_pool_preflight_enabled", label: "注册前预检", type: "switch" },
    ],
  },
  {
    id: "cpa",
    label: "CPA 授权",
    fields: [
      { key: "cpa_export_enabled", label: "导出 CPA 凭据", type: "switch" },
      { key: "cpa_headless", label: "授权浏览器无头", type: "switch", hint: "开启后不弹窗" },
      { key: "cpa_force_standalone", label: "独立浏览器授权", type: "switch", hint: "关闭则复用注册会话，成功率更高" },
      { key: "cpa_mint_cookie_inject", label: "注入登录 Cookie", type: "switch" },
      { key: "cpa_auth_dir", label: "凭据输出目录" },
      { key: "cpa_base_url", label: "CPA 接口地址" },
      { key: "cpa_mint_timeout_sec", label: "授权超时(秒)", type: "number" },
    ],
  },
  {
    id: "pool",
    label: "自动入池",
    fields: [
      { key: "grok2api_auto_add_local", label: "注册后自动入本地池", type: "switch" },
      { key: "grok2api_local_token_file", label: "本地池 Token 文件" },
      { key: "grok2api_pool_name", label: "池名称" },
      { key: "grok2api_auto_add_remote", label: "注册后自动入远程池", type: "switch" },
      { key: "grok2api_remote_base", label: "远程池地址" },
      { key: "grok2api_remote_app_key", label: "远程 App Key", type: "password" },
      { key: "grok2api_remote_admin_username", label: "远程管理员账号" },
      { key: "grok2api_remote_admin_password", label: "远程管理员密码", type: "password" },
    ],
  },
];

const EMPTY_FORM = GROUPS.reduce<Record<string, unknown>>((acc, group) => {
  for (const field of group.fields) acc[field.key] = field.type === "switch" ? false : "";
  return acc;
}, {});

export default function GrokRegisterPage() {
  const { isCheckingAuth } = useAuthGuard(["admin"]);
  const [job, setJob] = useState<Record<string, unknown>>({});
  const [form, setForm] = useState<Record<string, unknown>>(EMPTY_FORM);
  const [baseUrl, setBaseUrl] = useState("http://127.0.0.1:8092");
  const [reachable, setReachable] = useState<boolean | null>(null);
  const [activeGroup, setActiveGroup] = useState("task");
  const [logs, setLogs] = useState<{ seq: number; line: string }[]>([]);
  const [nodes, setNodes] = useState<{ proxy: string; probe_status: string; exit_ip: string; latency_ms: number; error: string }[]>([]);
  const [poolText, setPoolText] = useState("");
  const [poolPath, setPoolPath] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const logRef = useRef<HTMLDivElement | null>(null);
  const seqRef = useRef(0);

  const loadSettings = useCallback(async () => {
    try {
      const data = await fetchRegisterSettings();
      setBaseUrl(data.settings.base_url);
      setReachable(data.reachable);
    } catch {
      setReachable(false);
    }
  }, []);

  const loadStatus = useCallback(async () => {
    try {
      const data = await fetchRegisterStatus();
      setJob(data.job || {});
      setForm((prev) => ({ ...EMPTY_FORM, ...prev, ...(data.config || {}) }));
      setReachable(true);
      setError("");
    } catch (err) {
      setReachable(false);
      setError(err instanceof Error ? err.message : "注册服务不可用");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadPool = useCallback(async () => {
    try {
      const data = await fetchRegisterPoolContent();
      setPoolText((data.lines || []).join("\n"));
      setPoolPath(data.path || "");
    } catch {
      /* 代理池读取失败不阻塞页面 */
    }
  }, []);

  useEffect(() => {
    if (isCheckingAuth) return;
    void loadSettings();
    void loadStatus();
    void loadPool();
  }, [isCheckingAuth, loadSettings, loadStatus, loadPool]);

  useEffect(() => {
    if (isCheckingAuth) return;
    const timer = window.setInterval(async () => {
      try {
        const data = await fetchRegisterLogs(seqRef.current);
        if (data.entries?.length) {
          seqRef.current = data.entries[data.entries.length - 1].seq;
          setLogs((prev) => [...prev.slice(-600), ...data.entries]);
        }
      } catch {
        /* 忽略轮询错误 */
      }
    }, 2500);
    return () => window.clearInterval(timer);
  }, [isCheckingAuth]);

  useEffect(() => {
    if (isCheckingAuth) return;
    const timer = window.setInterval(() => void loadStatus(), 6000);
    return () => window.clearInterval(timer);
  }, [isCheckingAuth, loadStatus]);

  useEffect(() => {
    const box = logRef.current;
    if (box) box.scrollTop = box.scrollHeight;
  }, [logs]);

  const running = Boolean(job.running);
  const group = GROUPS.find((g) => g.id === activeGroup) || GROUPS[0];

  function setValue(key: string, value: unknown) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  async function saveService() {
    setBusy("service");
    setError("");
    setNotice("");
    try {
      await saveRegisterSettings({ base_url: baseUrl });
      await loadSettings();
      await loadStatus();
      setNotice("注册服务地址已保存");
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存服务地址失败");
    } finally {
      setBusy("");
    }
  }

  async function saveConfig() {
    setBusy("save");
    setError("");
    setNotice("");
    try {
      const payload: Record<string, unknown> = {};
      for (const g of GROUPS) {
        for (const f of g.fields) {
          const value = form[f.key];
          if (value === undefined || value === null || value === "") continue;
          payload[f.key] = value;
        }
      }
      const data = await updateRegisterConfig(payload);
      setForm((prev) => ({ ...prev, ...(data.config || {}) }));
      setNotice("配置已写入注册服务");
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存配置失败");
    } finally {
      setBusy("");
    }
  }

  async function toggleRun() {
    setBusy(running ? "stop" : "start");
    setError("");
    setNotice("");
    try {
      if (running) {
        await stopRegister();
        setNotice("已发送停止请求");
      } else {
        await saveConfig();
        const data = await startRegister();
        setNotice(`已启动，目标 ${data.target ?? "-"} 个`);
      }
      await loadStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : "操作失败");
    } finally {
      setBusy("");
    }
  }

  async function testPool() {
    setBusy("pool");
    setError("");
    try {
      const data = await testRegisterPool();
      setNodes(data.nodes || []);
      if (!(data.nodes || []).length) setNotice("代理池为空，请先在「代理」里配置节点");
    } catch (err) {
      setError(err instanceof Error ? err.message : "代理池检测失败");
    } finally {
      setBusy("");
    }
  }

  async function savePool() {
    setBusy("pool");
    setError("");
    setNotice("");
    try {
      const lines = poolText.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
      const data = await saveRegisterPoolContent(lines, true);
      setPoolPath(data.path || "");
      if (data.config) setForm((prev) => ({ ...prev, ...data.config }));
      setNotice(`代理池已保存并加载：${lines.length} 个节点`);
      await testPool();
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存代理池失败");
    } finally {
      setBusy("");
    }
  }

  function renderField(field: Field) {
    const value = form[field.key];
    if (field.type === "switch") {
      return (
        <label key={field.key} className="flex items-center gap-2 py-2 text-sm text-stone-700">
          <input
            type="checkbox"
            checked={Boolean(value)}
            onChange={(e) => setValue(field.key, e.target.checked)}
            disabled={running}
            className="size-4 rounded border-stone-300"
          />
          <span>{field.label}</span>
          {field.hint ? <span className="text-[11px] text-stone-400">{field.hint}</span> : null}
        </label>
      );
    }
    if (field.type === "textarea") {
      const text = Array.isArray(value) ? value.join(",") : String(value ?? "");
      return (
        <label key={field.key} className="col-span-2 block space-y-1 text-xs text-stone-500">
          {field.label}
          {field.hint ? <span className="ml-1 text-stone-400">· {field.hint}</span> : null}
          <Textarea
            value={text}
            rows={3}
            onChange={(e) => setValue(field.key, e.target.value)}
            disabled={running}
            className="font-mono text-xs"
          />
        </label>
      );
    }
    if (field.type === "select") {
      return (
        <label key={field.key} className="block space-y-1 text-xs text-stone-500">
          {field.label}
          <select
            value={String(value ?? "")}
            onChange={(e) => setValue(field.key, e.target.value)}
            disabled={running}
            className="h-9 w-full rounded-lg border border-stone-200 bg-white px-2 text-sm text-stone-800"
          >
            <option value="">未设置</option>
            {(field.options || []).map((opt) => (
              <option key={opt} value={opt}>
                {opt}
              </option>
            ))}
          </select>
        </label>
      );
    }
    return (
      <label key={field.key} className="block space-y-1 text-xs text-stone-500">
        {field.label}
        {field.hint ? <span className="ml-1 text-stone-400">· {field.hint}</span> : null}
        <Input
          type={field.type === "password" ? "password" : field.type === "number" ? "number" : "text"}
          value={String(value ?? "")}
          onChange={(e) => setValue(field.key, field.type === "number" ? Number(e.target.value) : e.target.value)}
          disabled={running}
          className="font-mono text-xs"
        />
      </label>
    );
  }

  return (
    <div className="space-y-4 pt-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-[11px] font-medium uppercase tracking-[0.28em] text-stone-400">Grok</p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-stone-900">注册</h1>
          <p className="mt-1 text-sm text-stone-500">
            驱动独立的 grok-register 服务；邮箱、代理、出口都由这里配置。
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" onClick={() => void loadStatus()} disabled={Boolean(busy)}>
            <RefreshCw className="mr-1 size-4" /> 刷新
          </Button>
          <Button
            onClick={() => void toggleRun()}
            disabled={Boolean(busy) || loading || reachable === false}
            className={running ? "bg-rose-600 hover:bg-rose-700" : ""}
          >
            {busy === "start" || busy === "stop" ? (
              <LoaderCircle className="mr-1 size-4 animate-spin" />
            ) : running ? (
              <CircleStop className="mr-1 size-4" />
            ) : (
              <Play className="mr-1 size-4" />
            )}
            {running ? "停止" : "开始注册"}
          </Button>
        </div>
      </div>

      <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
        <CardContent className="flex flex-wrap items-end gap-3 p-4">
          <div className="flex items-center gap-2 text-xs text-stone-500">
            <Server className="size-4 text-stone-400" /> 注册服务地址
          </div>
          <Input
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            className="max-w-sm font-mono text-xs"
            placeholder="http://127.0.0.1:8092"
          />
          <Button variant="outline" onClick={() => void saveService()} disabled={Boolean(busy)}>
            {busy === "service" ? <LoaderCircle className="mr-1 size-4 animate-spin" /> : <Save className="mr-1 size-4" />}
            保存并连接
          </Button>
          <span className={`text-xs ${reachable ? "text-emerald-600" : "text-rose-600"}`}>
            {reachable === null ? "检测中" : reachable ? "已连接" : "不可达"}
          </span>
        </CardContent>
      </Card>

      {error ? (
        <div className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-2 text-sm text-rose-700">{error}</div>
      ) : null}
      {notice ? (
        <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-2 text-sm text-emerald-700">{notice}</div>
      ) : null}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        {[
          { label: "状态", value: running ? "运行中" : "空闲", accent: running ? "text-emerald-600" : "text-stone-500" },
          { label: "目标", value: String(job.target ?? 0), accent: "text-stone-900" },
          { label: "成功", value: String(job.success ?? 0), accent: "text-emerald-600" },
          { label: "失败", value: String(job.fail ?? 0), accent: "text-rose-600" },
          { label: "警告/不确定", value: `${job.warnings ?? 0}/${job.uncertain ?? 0}`, accent: "text-amber-600" },
        ].map((item) => (
          <Card key={item.label} className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
            <CardContent className="p-4">
              <p className="text-[11px] uppercase tracking-[0.2em] text-stone-400">{item.label}</p>
              <p className={`mt-1 text-xl font-semibold ${item.accent}`}>{item.value}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      <div className="grid gap-4 lg:grid-cols-5">
        <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm lg:col-span-2">
          <CardContent className="space-y-3 p-5">
            <div className="flex items-center gap-2 text-sm font-medium text-stone-800">
              <UserPlus className="size-4 text-stone-400" /> 配置
            </div>
            <div className="flex flex-wrap gap-1">
              {GROUPS.map((g) => (
                <button
                  key={g.id}
                  type="button"
                  onClick={() => setActiveGroup(g.id)}
                  className={`rounded-lg px-3 py-1.5 text-xs transition ${
                    g.id === activeGroup
                      ? "bg-stone-900 text-white"
                      : "bg-stone-100 text-stone-600 hover:bg-stone-200"
                  }`}
                >
                  {g.label}
                </button>
              ))}
            </div>
            <div className="grid grid-cols-2 gap-3">
              {group.fields.map((field) => renderField(field))}
            </div>
            {activeGroup === "proxy" ? (
              <div className="space-y-2 rounded-xl border border-stone-200 bg-stone-50/70 p-3">
                <div className="flex items-center justify-between gap-2 text-xs text-stone-500">
                  <span>代理池节点（每行一个）</span>
                  <span className="truncate font-mono text-[11px] text-stone-400">{poolPath || "尚未保存"}</span>
                </div>
                <Textarea
                  value={poolText}
                  onChange={(e) => setPoolText(e.target.value)}
                  rows={6}
                  disabled={running}
                  placeholder={"socks5://user:pass@1.2.3.4:1080\nhttp://5.6.7.8:8080\nvless://uuid@host:443?..."}
                  className="font-mono text-xs"
                />
                <div className="flex items-center gap-2">
                  <Button variant="outline" onClick={() => void savePool()} disabled={running || Boolean(busy)}>
                    {busy === "pool" ? <LoaderCircle className="mr-1 size-4 animate-spin" /> : <Save className="mr-1 size-4" />}
                    保存并应用
                  </Button>
                  <Button variant="outline" onClick={() => void loadPool()} disabled={running || Boolean(busy)}>
                    <RefreshCw className="mr-1 size-4" /> 重新读取
                  </Button>
                </div>
                <p className="text-[11px] leading-relaxed text-stone-400">
                  支持 ip:port、http://、socks5:// 以及 vmess / vless / trojan / ss 链接。保存后自动把
                  proxy_mode 设为 pool、把本文件路径写进注册服务，并重新加载节点。
                </p>
              </div>
            ) : null}
            <div className="flex items-center gap-2">
              <Button variant="outline" onClick={() => void saveConfig()} disabled={running || Boolean(busy) || reachable === false}>
                {busy === "save" ? <LoaderCircle className="mr-1 size-4 animate-spin" /> : <Save className="mr-1 size-4" />}
                保存配置
              </Button>
              <Button variant="outline" onClick={() => void testPool()} disabled={Boolean(busy) || reachable === false}>
                {busy === "pool" ? <LoaderCircle className="mr-1 size-4 animate-spin" /> : <RefreshCw className="mr-1 size-4" />}
                检测代理池
              </Button>
            </div>
            {nodes.length ? (
              <div className="space-y-1 rounded-xl bg-stone-50 p-3 text-xs">
                {nodes.map((n) => (
                  <div key={n.proxy} className="flex items-center justify-between gap-2">
                    <span className="truncate font-mono text-stone-600">{n.proxy}</span>
                    <span className={n.probe_status === "healthy" ? "text-emerald-600" : "text-rose-600"}>
                      {n.exit_ip || n.error || n.probe_status}
                      {n.latency_ms ? ` · ${n.latency_ms}ms` : ""}
                    </span>
                  </div>
                ))}
              </div>
            ) : null}
          </CardContent>
        </Card>

        <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm lg:col-span-3">
          <CardContent className="p-5">
            <div className="mb-2 flex items-center justify-between">
              <span className="text-sm font-medium text-stone-800">实时日志</span>
              <span className="text-xs text-stone-400">{logs.length} 条</span>
            </div>
            <div
              ref={logRef}
              className="h-[520px] overflow-auto rounded-xl bg-stone-950 p-3 font-mono text-[11px] leading-relaxed text-stone-200"
            >
              {logs.length === 0 ? (
                <p className="text-stone-500">等待日志…</p>
              ) : (
                logs.map((l) => (
                  <div key={l.seq} className="whitespace-pre-wrap break-all">
                    {l.line}
                  </div>
                ))
              )}
            </div>
            {job.accounts_file ? (
              <p className="mt-2 truncate text-[11px] text-stone-400">账号文件：{String(job.accounts_file)}</p>
            ) : null}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

"use client";

import { useEffect, useMemo, useState } from "react";
import { Activity, CircleAlert, Cpu, Gauge, LoaderCircle, RefreshCw, Wallet } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { fetchGrokDashboard, type GrokDashboard } from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";

const PROVIDER_LABEL: Record<string, string> = {
  grok_web: "Grok Web",
  grok_build: "Grok Build",
  grok_console: "Grok Console",
};

const RANGES = ["24h", "7d", "30d", "90d"];

function usd(ticks?: number) {
  return `$${((ticks || 0) / 1e9).toFixed(2)}`;
}

function Kpi({ label, value, hint, icon: Icon }: { label: string; value: string; hint: string; icon: typeof Cpu }) {
  return (
    <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
      <CardContent className="space-y-1.5 p-5">
        <div className="flex items-center justify-between text-xs text-stone-400">
          <span>{label}</span>
          <Icon className="size-4" />
        </div>
        <div className="text-3xl font-semibold tracking-tight text-stone-900">{value}</div>
        <p className="text-xs text-stone-400">{hint}</p>
      </CardContent>
    </Card>
  );
}

export default function GrokDashboardPage() {
  const { isCheckingAuth } = useAuthGuard(["admin"]);
  const [data, setData] = useState<GrokDashboard | null>(null);
  const [period, setPeriod] = useState("24h");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = async (range = period) => {
    setLoading(true);
    try {
      setData(await fetchGrokDashboard(range));
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "读取失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!isCheckingAuth) void load(period);
  }, [isCheckingAuth, period]);

  const resources = data?.resources;
  const usage = data?.usage;
  const series = data?.series || [];
  const maxRequests = useMemo(() => Math.max(1, ...series.map((item) => item.requests)), [series]);
  const cacheHit = usage && usage.tokens ? Math.round((usage.cachedInputTokens / Math.max(1, usage.inputTokens + usage.cachedInputTokens)) * 100) : 0;

  return (
    <div className="space-y-5 pt-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-[11px] font-medium uppercase tracking-[0.28em] text-stone-400">Grok</p>
          <h1 className="mt-1 text-3xl font-semibold tracking-tight text-stone-900">仪表盘</h1>
          <p className="mt-1 text-sm text-stone-500">Grok 号池用量、计费与资源状态。</p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex rounded-xl bg-stone-100 p-1">
            {RANGES.map((range) => (
              <button
                key={range}
                type="button"
                onClick={() => setPeriod(range)}
                className={`rounded-lg px-3 py-1 text-xs font-medium transition ${period === range ? "bg-white text-stone-900 shadow-sm" : "text-stone-500"}`}
              >
                {range}
              </button>
            ))}
          </div>
          <Button variant="ghost" className="h-9 rounded-xl" onClick={() => void load()} disabled={loading}>
            {loading ? <LoaderCircle className="size-4 animate-spin" /> : <RefreshCw className="size-4" />} 刷新
          </Button>
        </div>
      </div>

      {error ? (
        <Card className="rounded-2xl border-rose-200 bg-rose-50/60">
          <CardContent className="flex items-center gap-2 p-4 text-sm text-rose-600">
            <CircleAlert className="size-4" /> {error}
          </CardContent>
        </Card>
      ) : null}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
        <Kpi label="账号数" value={String(resources?.totalAccounts ?? 0)} hint={`Build ${resources?.buildAccounts ?? 0} · Web ${resources?.webAccounts ?? 0} · Console ${resources?.consoleAccounts ?? 0}`} icon={Cpu} />
        <Kpi label="请求数" value={String(usage?.requests ?? 0)} hint={`成功率 ${usage?.successRate ?? 0}%`} icon={Activity} />
        <Kpi label="总 Tokens" value={(usage?.tokens ?? 0).toLocaleString()} hint={`缓存命中 ${cacheHit}%`} icon={Gauge} />
        <Kpi label="计费" value={usd(usage?.billedCostUsdTicks)} hint={`平均每次 ${usd((usage?.billedCostUsdTicks || 0) / Math.max(1, usage?.requests || 1))}`} icon={Wallet} />
        <Kpi label="平均首字" value={`${((usage?.averageFirstTokenMs ?? 0) / 1000).toFixed(2)} s`} hint={`输出 ${usage?.outputTokensPerSecond ?? 0} tok/s`} icon={Activity} />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm lg:col-span-2">
          <CardContent className="p-5">
            <h2 className="mb-4 text-sm font-semibold text-stone-800">用量趋势</h2>
            <div className="flex h-40 items-end gap-1">
              {series.map((item, index) => (
                <div key={index} className="group relative flex-1">
                  <div
                    className="w-full rounded-t bg-indigo-400/80 transition group-hover:bg-indigo-500"
                    style={{ height: `${Math.max(2, (item.requests / maxRequests) * 150)}px` }}
                  />
                  <span className="pointer-events-none absolute -top-6 left-1/2 hidden -translate-x-1/2 rounded bg-stone-900 px-1.5 py-0.5 text-[10px] text-white group-hover:block">
                    {item.requests}
                  </span>
                </div>
              ))}
            </div>
            <div className="mt-2 flex justify-between text-[10px] text-stone-400">
              <span>{series[0]?.start?.slice(5, 16).replace("T", " ")}</span>
              <span>{series[series.length - 1]?.end?.slice(5, 16).replace("T", " ")}</span>
            </div>
          </CardContent>
        </Card>

        <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
          <CardContent className="space-y-4 p-5">
            <h2 className="text-sm font-semibold text-stone-800">渠道分布</h2>
            {(data?.providers || []).map((provider) => {
              const share = usage?.requests ? Math.round((provider.requests / usage.requests) * 100) : 0;
              return (
                <div key={provider.provider} className="space-y-1">
                  <div className="flex items-center justify-between text-xs text-stone-500">
                    <span>{PROVIDER_LABEL[provider.provider] || provider.provider}</span>
                    <span>{provider.requests} 次 · {provider.tokens.toLocaleString()} tokens</span>
                  </div>
                  <div className="h-2 w-full overflow-hidden rounded-full bg-stone-100">
                    <div className="h-full rounded-full bg-indigo-500" style={{ width: `${Math.max(2, share)}%` }} />
                  </div>
                </div>
              );
            })}
            {(data?.providers || []).length === 0 ? <p className="text-xs text-stone-400">窗口内没有请求</p> : null}
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
          <CardContent className="flex items-center gap-5 p-5">
            <svg viewBox="0 0 36 36" className="size-24">
              <circle cx="18" cy="18" r="15.5" fill="none" stroke="#eceef3" strokeWidth="4" />
              <circle
                cx="18" cy="18" r="15.5" fill="none" stroke="#4f46e5" strokeWidth="4"
                strokeDasharray={`${(resources?.totalAccounts ? (resources.activeAccounts / resources.totalAccounts) * 97.4 : 0)} 97.4`}
                strokeLinecap="round" transform="rotate(-90 18 18)"
              />
            </svg>
            <div className="space-y-1">
              <p className="text-xs text-stone-400">资源可用性</p>
              <p className="text-2xl font-semibold text-stone-900">
                {resources?.activeAccounts ?? 0}<span className="text-sm text-stone-400"> / {resources?.totalAccounts ?? 0}</span>
              </p>
              <p className="text-xs text-stone-400">
                模型 {resources?.enabledModels ?? 0}/{resources?.totalModels ?? 0} · 密钥 {resources?.activeClientKeys ?? 0}/{resources?.totalClientKeys ?? 0}
              </p>
            </div>
          </CardContent>
        </Card>
        <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm lg:col-span-2">
          <CardContent className="p-5">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-sm font-semibold text-stone-800">请求活跃度</h2>
              <span className="text-xs text-stone-400">近 180 天</span>
            </div>
            <div className="flex flex-wrap gap-1">
              {(data?.activity || []).map((item, index) => {
                const max = Math.max(1, ...(data?.activity || []).map((a) => a.requests));
                const level = item.requests === 0 ? 0 : Math.ceil((item.requests / max) * 4);
                const colors = ["bg-stone-100", "bg-emerald-100", "bg-emerald-200", "bg-emerald-400", "bg-emerald-600"];
                return <div key={index} title={`${item.start.slice(0, 10)} · ${item.requests} 次`} className={`size-3 rounded-sm ${colors[level]}`} />;
              })}
            </div>
          </CardContent>
        </Card>
      </div>

      <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
        <CardContent className="p-0">
          <div className="border-b border-stone-100 px-5 py-3 text-sm font-semibold text-stone-800">模型计费 Top 10</div>
          <table className="w-full text-left text-sm">
            <thead className="text-[11px] uppercase tracking-[0.18em] text-stone-400">
              <tr>
                <th className="px-5 py-2">模型</th>
                <th className="px-5 py-2">请求</th>
                <th className="px-5 py-2">输入 / 输出</th>
                <th className="px-5 py-2">Tokens</th>
                <th className="px-5 py-2">计费</th>
              </tr>
            </thead>
            <tbody>
              {(data?.topModels || []).map((model) => (
                <tr key={model.model} className="border-t border-stone-100/80">
                  <td className="px-5 py-2.5 font-medium text-stone-700">{model.model}</td>
                  <td className="px-5 py-2.5 text-stone-500">{model.requests}</td>
                  <td className="px-5 py-2.5 text-stone-500">{model.inputTokens?.toLocaleString()} / {model.outputTokens?.toLocaleString()}</td>
                  <td className="px-5 py-2.5 text-stone-500">{model.tokens.toLocaleString()}</td>
                  <td className="px-5 py-2.5 text-stone-500">{usd(model.billedCostUsdTicks)}</td>
                </tr>
              ))}
              {(data?.topModels || []).length === 0 ? (
                <tr><td colSpan={5} className="px-5 py-8 text-center text-stone-400">窗口内没有调用</td></tr>
              ) : null}
            </tbody>
          </table>
        </CardContent>
      </Card>
    </div>
  );
}

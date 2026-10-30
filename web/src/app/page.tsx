"use client";

import { useEffect, useMemo, useState } from "react";
import { Activity, Boxes, CircleAlert, Gauge, Layers, LoaderCircle, Users } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import { fetchAccounts, fetchGrokOverview, type Account, type GrokSummary } from "@/lib/api";
import { httpRequest } from "@/lib/request";
import { useAuthGuard } from "@/lib/use-auth-guard";

type MetricsSummary = {
  total: number;
  failed: number;
  success_rate: number | null;
  rpm: number;
  p95_ms: number | null;
};

function Kpi({
  label,
  value,
  hint,
  icon: Icon,
  accent = "text-stone-900",
}: {
  label: string;
  value: string;
  hint?: string;
  icon: typeof Users;
  accent?: string;
}) {
  return (
    <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
      <CardContent className="space-y-2 p-5">
        <div className="flex items-center justify-between text-xs text-stone-400">
          <span>{label}</span>
          <Icon className="size-4" />
        </div>
        <div className={`text-3xl font-semibold tracking-tight ${accent}`}>{value}</div>
        {hint ? <p className="text-xs text-stone-400">{hint}</p> : null}
      </CardContent>
    </Card>
  );
}

export default function HomePage() {
  const { isCheckingAuth } = useAuthGuard(["admin"]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [grok, setGrok] = useState<GrokSummary | null>(null);
  const [metrics, setMetrics] = useState<MetricsSummary | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (isCheckingAuth) return;
    let active = true;
    const run = async () => {
      try {
        const [accountData, grokData, metricData] = await Promise.all([
          fetchAccounts(),
          fetchGrokOverview().catch(() => ({ summary: {}, keys: 0 })),
          httpRequest<MetricsSummary>("/api/metrics/summary?window_seconds=3600"),
        ]);
        if (!active) return;
        setAccounts(accountData.items || []);
        setGrok(grokData.summary || {});
        setMetrics(metricData);
      } catch {
        // 仪表盘失败不阻塞页面
      } finally {
        if (active) setLoading(false);
      }
    };
    void run();
    const timer = setInterval(() => void run(), 30000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [isCheckingAuth]);

  const stats = useMemo(() => {
    const byStatus: Record<string, number> = {};
    for (const account of accounts) {
      const status = String(account.status || "未知");
      byStatus[status] = (byStatus[status] || 0) + 1;
    }

    return {
      total: accounts.length,
      byStatus,
      normal: byStatus["正常"] || 0,
      limited: byStatus["限流"] || 0,
      abnormal: byStatus["异常"] || 0,
      disabled: byStatus["禁用"] || 0,
      grokTotal: grok?.total ?? 0,
      grokAvailable: grok?.available ?? 0,
      grokRecovering: grok?.recovering ?? 0,
    };
  }, [accounts, grok]);

  const healthTotal = Math.max(stats.total, 1);

  return (
    <div className="space-y-5 pt-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-[11px] font-medium uppercase tracking-[0.28em] text-stone-400">Dashboard</p>
          <h1 className="mt-1 text-3xl font-semibold tracking-tight text-stone-900">总览</h1>
          <p className="mt-1 text-sm text-stone-500">所有已接入号池与上游的汇总，每 30 秒自动刷新。</p>
        </div>
        {loading ? (
          <span className="flex items-center gap-2 text-xs text-stone-400">
            <LoaderCircle className="size-4 animate-spin" /> 同步中
          </span>
        ) : null}
      </div>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
        <Kpi label="账号总数（GPT）" value={String(stats.total)} hint={`正常 ${stats.normal} · 限流 ${stats.limited}`} icon={Users} />
        <Kpi label="Grok 账号" value={String(stats.grokTotal)} hint={`可用 ${stats.grokAvailable}`} icon={Boxes} />
        <Kpi label="Grok 恢复中" value={String(stats.grokRecovering)} hint="等待配额重置" icon={Layers} />
        <Kpi
          label="成功率（1h）"
          value={metrics?.success_rate != null ? `${Math.round(metrics.success_rate * 1000) / 10}%` : "—"}
          hint={`${metrics?.total ?? 0} 次请求`}
          icon={Activity}
        />
        <Kpi
          label="P95 延迟（1h）"
          value={metrics?.p95_ms != null ? `${Math.round(metrics.p95_ms)} ms` : "—"}
          hint={`RPM ${metrics?.rpm ?? 0}`}
          icon={Gauge}
        />
      </div>

      <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
        <CardContent className="space-y-3 p-5">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-stone-800">GPT 账号健康分布</h2>
            <span className="text-xs text-stone-400">共 {stats.total} 个</span>
          </div>
          <div className="flex h-3 w-full overflow-hidden rounded-full bg-stone-100">
            <div className="bg-emerald-500" style={{ width: `${(stats.normal / healthTotal) * 100}%` }} />
            <div className="bg-amber-400" style={{ width: `${(stats.limited / healthTotal) * 100}%` }} />
            <div className="bg-rose-500" style={{ width: `${(stats.abnormal / healthTotal) * 100}%` }} />
            <div className="bg-stone-400" style={{ width: `${(stats.disabled / healthTotal) * 100}%` }} />
          </div>
          <div className="flex flex-wrap gap-4 text-xs text-stone-500">
            <span className="flex items-center gap-1.5"><i className="size-2 rounded-full bg-emerald-500" />正常 {stats.normal}</span>
            <span className="flex items-center gap-1.5"><i className="size-2 rounded-full bg-amber-400" />限流 {stats.limited}</span>
            <span className="flex items-center gap-1.5"><i className="size-2 rounded-full bg-rose-500" />异常 {stats.abnormal}</span>
            <span className="flex items-center gap-1.5"><i className="size-2 rounded-full bg-stone-400" />禁用 {stats.disabled}</span>
          </div>
        </CardContent>
      </Card>

    </div>
  );
}

"use client";

import { useEffect, useMemo, useState } from "react";
import { Activity, Boxes, CircleAlert, Cpu, Gauge, LoaderCircle, RefreshCw, Users } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { fetchMinimaxDashboard, fetchMinimaxStatus, type MinimaxStatus } from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";

type Dashboard = {
  activity?: Array<{ id: string; time: string; model: string; status: number; latencyMs: number; account: string }>;
  distribution?: Array<{ type: string; count: number }>;
  resources?: Record<string, number>;
  topModels?: Array<{ model: string; requests: number; tokens: number }>;
  trend?: Array<{ bucket: string; requests: number; failures: number; tokens: number }>;
  upstream?: Record<string, any>;
  usage?: Record<string, any>;
};

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

export default function MinimaxDashboardPage() {
  const { isCheckingAuth } = useAuthGuard(["admin"]);
  const [data, setData] = useState<Dashboard | null>(null);
  const [status, setStatus] = useState<MinimaxStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = async () => {
    setLoading(true);
    try {
      const [board, st] = await Promise.all([fetchMinimaxDashboard(), fetchMinimaxStatus()]);
      setData(board);
      setStatus(st);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "读取失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!isCheckingAuth) void load();
  }, [isCheckingAuth]);

  const resources = data?.resources;
  const usage = data?.usage;
  const upstream = data?.upstream;
  const trend = data?.trend || [];
  const maxRequests = useMemo(() => Math.max(1, ...trend.map((item) => item.requests)), [trend]);

  return (
    <div className="space-y-5 pt-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-stone-900">MiniMax 仪表盘</h1>
          <p className="mt-1 text-sm text-stone-500">
            minimax2api 号池网关的统一视图 ·{" "}
            <span className={status?.reachable ? "text-emerald-600" : "text-rose-500"}>
              {status?.reachable ? "已连通" : "未连通"}
            </span>
            {status?.base_url ? ` · ${status.base_url}` : ""}
            {status?.tunnel_running ? " · 隧道在线" : ""}
          </p>
        </div>
        <Button variant="outline" onClick={() => void load()} disabled={loading}>
          {loading ? <LoaderCircle className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
          刷新
        </Button>
      </div>

      {error ? (
        <Card className="rounded-2xl border-rose-200 bg-rose-50/70">
          <CardContent className="flex items-start gap-2 p-4 text-sm text-rose-700">
            <CircleAlert className="mt-0.5 size-4 shrink-0" />
            <span>{error}</span>
          </CardContent>
        </Card>
      ) : null}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi
          label="账号总数"
          value={String(resources?.totalAccounts ?? "-")}
          hint={`可路由 ${resources?.routableAccounts ?? "-"} · 无效 ${resources?.invalidAccounts ?? "-"}`}
          icon={Users}
        />
        <Kpi
          label="活跃账号"
          value={String(resources?.activeAccounts ?? "-")}
          hint={`冷却 ${resources?.cooldownAccounts ?? "-"} · 停用 ${resources?.disabledAccounts ?? "-"}`}
          icon={Activity}
        />
        <Kpi
          label="请求数"
          value={String(usage?.requests ?? "-")}
          hint={`成功率 ${usage?.successRate ?? 0}% · 失败 ${usage?.failedRequests ?? 0}`}
          icon={Gauge}
        />
        <Kpi
          label="平均延迟"
          value={`${usage?.averageLatencyMs ?? 0} ms`}
          hint={`首字 ${usage?.averageFirstTokenMs ?? 0} ms · 上游池 ${upstream?.poolAvailable ?? "-"}/${upstream?.poolTotal ?? "-"}`}
          icon={Cpu}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
          <CardContent className="space-y-3 p-5">
            <div className="text-sm font-semibold text-stone-900">模型调用 Top</div>
            {(data?.topModels || []).length === 0 ? (
              <p className="text-sm text-stone-400">暂无数据</p>
            ) : (
              <div className="space-y-2">
                {(data?.topModels || []).map((item) => (
                  <div key={item.model} className="flex items-center gap-3 text-sm">
                    <span className="w-40 shrink-0 truncate text-stone-600">{item.model}</span>
                    <div className="h-2 flex-1 overflow-hidden rounded-full bg-stone-100">
                      <div
                        className="h-full rounded-full bg-emerald-500"
                        style={{ width: `${Math.min(100, (item.requests / Math.max(1, (data?.topModels?.[0]?.requests || 1))) * 100)}%` }}
                      />
                    </div>
                    <span className="w-20 shrink-0 text-right text-xs text-stone-400">
                      {item.requests} 次 / {item.tokens} tok
                    </span>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
          <CardContent className="space-y-3 p-5">
            <div className="text-sm font-semibold text-stone-900">请求趋势</div>
            {trend.length === 0 ? (
              <p className="text-sm text-stone-400">暂无数据</p>
            ) : (
              <div className="space-y-2">
                {trend.map((item) => (
                  <div key={item.bucket} className="flex items-center gap-3 text-sm">
                    <span className="w-28 shrink-0 text-stone-500">{item.bucket}</span>
                    <div className="h-2 flex-1 overflow-hidden rounded-full bg-stone-100">
                      <div
                        className="h-full rounded-full bg-indigo-500"
                        style={{ width: `${(item.requests / maxRequests) * 100}%` }}
                      />
                    </div>
                    <span className="w-24 shrink-0 text-right text-xs text-stone-400">
                      {item.requests} 次 / 失败 {item.failures}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
        <CardContent className="space-y-3 p-5">
          <div className="flex items-center gap-2 text-sm font-semibold text-stone-900">
            <Boxes className="size-4" />
            最近请求
          </div>
          {(data?.activity || []).length === 0 ? (
            <p className="text-sm text-stone-400">暂无请求</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="text-xs text-stone-400">
                  <tr>
                    <th className="py-2 pr-4 font-normal">时间</th>
                    <th className="py-2 pr-4 font-normal">模型</th>
                    <th className="py-2 pr-4 font-normal">账号</th>
                    <th className="py-2 pr-4 font-normal">状态</th>
                    <th className="py-2 font-normal">延迟</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-stone-100">
                  {(data?.activity || []).map((row) => (
                    <tr key={row.id} className="text-stone-600">
                      <td className="py-2 pr-4 text-xs">{String(row.time || "").slice(0, 19).replace("T", " ")}</td>
                      <td className="py-2 pr-4">{row.model}</td>
                      <td className="py-2 pr-4 text-xs">{row.account}</td>
                      <td className="py-2 pr-4">
                        <span className={row.status >= 400 ? "text-rose-500" : "text-emerald-600"}>{row.status}</span>
                      </td>
                      <td className="py-2 text-xs">{row.latencyMs} ms</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

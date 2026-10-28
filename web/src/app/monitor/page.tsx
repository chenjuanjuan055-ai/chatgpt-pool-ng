"use client";

import { useCallback, useEffect, useState } from "react";
import { Activity, LoaderCircle, RefreshCw, ShieldAlert, Trash2, Zap } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  cleanupDeadAccounts,
  fetchAccountHealth,
  fetchEgressPool,
  fetchMetricsSummary,
  probeEgressPool,
  type AccountHealthSummary,
  type EgressEndpointStatus,
  type EgressPoolView,
  type MetricsSummary,
} from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";

const stateLabels: Record<string, string> = {
  healthy: "健康",
  failed: "失败",
  unknown: "未知",
  cooling: "冷却中",
};

function stateVariant(state: string): "default" | "secondary" | "danger" | "outline" {
  if (state === "healthy") return "default";
  if (state === "cooling") return "secondary";
  if (state === "failed") return "danger";
  return "outline";
}

function formatPercent(value: number | null | undefined) {
  if (value === null || value === undefined) return "-";
  return `${(value * 100).toFixed(1)}%`;
}

function formatLatency(value: number | null | undefined) {
  if (value === null || value === undefined) return "-";
  return `${Math.round(value)} ms`;
}

export default function MonitorPage() {
  const { isCheckingAuth } = useAuthGuard(["admin"]);
  const [metrics, setMetrics] = useState<MetricsSummary | null>(null);
  const [egress, setEgress] = useState<EgressPoolView | null>(null);
  const [health, setHealth] = useState<AccountHealthSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [probing, setProbing] = useState(false);
  const [cleaning, setCleaning] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [metricsData, egressData, healthData] = await Promise.all([
        fetchMetricsSummary(3600),
        fetchEgressPool(),
        fetchAccountHealth(),
      ]);
      setMetrics(metricsData);
      setEgress(egressData);
      setHealth(healthData);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "加载监控数据失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!isCheckingAuth) {
      void load();
    }
  }, [isCheckingAuth, load]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (!document.hidden) {
        void load();
      }
    }, 30_000);
    return () => window.clearInterval(timer);
  }, [load]);

  const handleProbe = async () => {
    setProbing(true);
    try {
      const result = await probeEgressPool();
      setEgress((prev) => (prev ? { ...prev, endpoints: result.endpoints } : prev));
      toast.success("探活完成");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "探活失败");
    } finally {
      setProbing(false);
    }
  };

  const handleCleanup = async (apply: boolean) => {
    if (apply && !window.confirm("确认删除所有死号？此操作不可撤销。")) {
      return;
    }
    setCleaning(true);
    try {
      const result = await cleanupDeadAccounts(apply, false);
      if (apply) {
        toast.success(`已删除 ${result.deleted} 个死号`);
        await load();
      } else {
        toast.info(`匹配到 ${result.matched} 个死号`);
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "清理失败");
    } finally {
      setCleaning(false);
    }
  };

  if (isCheckingAuth) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center text-muted-foreground">
        <LoaderCircle className="mr-2 size-4 animate-spin" /> 正在校验权限…
      </div>
    );
  }

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-4 p-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">监控</h1>
          <p className="text-sm text-muted-foreground">账号健康、出口池状态与请求指标（每 30 秒自动刷新）</p>
        </div>
        <Button variant="outline" onClick={() => void load()} disabled={loading}>
          {loading ? <LoaderCircle className="mr-1 size-4 animate-spin" /> : <RefreshCw className="mr-1 size-4" />}
          刷新
        </Button>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Card>
          <CardContent className="p-4">
            <div className="text-sm text-muted-foreground">成功率（1h）</div>
            <div className="mt-1 text-2xl font-semibold">{formatPercent(metrics?.success_rate)}</div>
            <div className="text-xs text-muted-foreground">
              {metrics ? `${metrics.success}/${metrics.total} 成功` : "-"}
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <div className="text-sm text-muted-foreground">RPM</div>
            <div className="mt-1 text-2xl font-semibold">{metrics?.rpm ?? 0}</div>
            <div className="text-xs text-muted-foreground">近 1 分钟请求数</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <div className="text-sm text-muted-foreground">延迟 P50 / P95</div>
            <div className="mt-1 text-2xl font-semibold">
              {formatLatency(metrics?.p50_ms)} / {formatLatency(metrics?.p95_ms)}
            </div>
            <div className="text-xs text-muted-foreground">仅统计已记录耗时的请求</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <div className="text-sm text-muted-foreground">失败数（1h）</div>
            <div className="mt-1 text-2xl font-semibold">{metrics?.failed ?? 0}</div>
            <div className="text-xs text-muted-foreground">按错误分类见下方</div>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardContent className="p-4">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2 font-medium">
              <ShieldAlert className="size-4" /> 账号健康
            </div>
            <div className="flex gap-2">
              <Button size="sm" variant="outline" disabled={cleaning} onClick={() => void handleCleanup(false)}>
                预览死号
              </Button>
              <Button size="sm" variant="destructive" disabled={cleaning} onClick={() => void handleCleanup(true)}>
                {cleaning ? <LoaderCircle className="mr-1 size-4 animate-spin" /> : <Trash2 className="mr-1 size-4" />}
                清理死号
              </Button>
            </div>
          </div>
          <div className="flex flex-wrap gap-2 text-sm">
            <Badge variant="outline">总数 {health?.total ?? 0}</Badge>
            <Badge>健康 {health?.healthy ?? 0}</Badge>
            <Badge variant="danger">死号 {health?.dead ?? 0}</Badge>
            <Badge variant="secondary">仅 AT（不可续期）{health?.at_only ?? 0}</Badge>
          </div>
          {health && Object.keys(health.status_counts).length > 0 && (
            <div className="mt-3 flex flex-wrap gap-2 text-xs text-muted-foreground">
              {Object.entries(health.status_counts).map(([status, count]) => (
                <span key={status} className="rounded border px-2 py-1">
                  {health.status_labels[status] || status}：{count}
                </span>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardContent className="p-4">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2 font-medium">
              <Zap className="size-4" /> 出口池
              <Badge variant={egress?.enabled ? "default" : "outline"}>{egress?.enabled ? "已启用" : "未启用"}</Badge>
            </div>
            <Button size="sm" variant="outline" disabled={probing} onClick={() => void handleProbe()}>
              {probing ? <LoaderCircle className="mr-1 size-4 animate-spin" /> : <Activity className="mr-1 size-4" />}
              立即探活
            </Button>
          </div>
          {egress && egress.endpoints.length > 0 ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>出口</TableHead>
                  <TableHead>状态</TableHead>
                  <TableHead>延迟</TableHead>
                  <TableHead>连续失败</TableHead>
                  <TableHead>冷却剩余</TableHead>
                  <TableHead>最近错误</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {egress.endpoints.map((endpoint: EgressEndpointStatus) => (
                  <TableRow key={endpoint.url}>
                    <TableCell>
                      <div className="font-mono text-xs">{endpoint.url}</div>
                      {endpoint.label ? <div className="text-xs text-muted-foreground">{endpoint.label}</div> : null}
                    </TableCell>
                    <TableCell>
                      <Badge variant={stateVariant(endpoint.state)}>{stateLabels[endpoint.state] || endpoint.state}</Badge>
                    </TableCell>
                    <TableCell>{formatLatency(endpoint.latency_ms)}</TableCell>
                    <TableCell>{endpoint.consecutive_failures}</TableCell>
                    <TableCell>{endpoint.cooldown_remaining ? `${Math.ceil(endpoint.cooldown_remaining)} s` : "-"}</TableCell>
                    <TableCell className="max-w-[280px] truncate text-xs text-muted-foreground" title={endpoint.last_error}>
                      {endpoint.last_error || "-"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <p className="text-sm text-muted-foreground">
              未配置出口池。调用 `POST /api/proxy/egress/settings` 添加出口后，这里会显示每条线路的健康状态。
            </p>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-3 lg:grid-cols-2">
        <Card>
          <CardContent className="p-4">
            <div className="mb-3 font-medium">按模型</div>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>模型</TableHead>
                  <TableHead>请求</TableHead>
                  <TableHead>失败</TableHead>
                  <TableHead>成功率</TableHead>
                  <TableHead>P95</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(metrics?.by_model || []).slice(0, 8).map((row) => (
                  <TableRow key={row.model}>
                    <TableCell className="font-mono text-xs">{row.model}</TableCell>
                    <TableCell>{row.total}</TableCell>
                    <TableCell>{row.failed}</TableCell>
                    <TableCell>{formatPercent(row.success_rate)}</TableCell>
                    <TableCell>{formatLatency(row.p95_ms)}</TableCell>
                  </TableRow>
                ))}
                {!metrics?.by_model?.length && (
                  <TableRow>
                    <TableCell colSpan={5} className="text-center text-sm text-muted-foreground">
                      暂无调用数据
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-4">
            <div className="mb-3 font-medium">错误分类</div>
            <div className="flex flex-col gap-2">
              {(metrics?.by_error_category || []).map((row) => (
                <div key={row.category} className="flex items-center justify-between rounded border px-3 py-2 text-sm">
                  <div>
                    <div className="font-medium">{row.label || row.category}</div>
                    <div className="text-xs text-muted-foreground">{row.hint}</div>
                  </div>
                  <Badge variant="secondary">{row.count}</Badge>
                </div>
              ))}
              {!metrics?.by_error_category?.length && (
                <p className="text-sm text-muted-foreground">窗口内没有失败请求</p>
              )}
            </div>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardContent className="p-4">
          <div className="mb-3 font-medium">最近失败</div>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>时间</TableHead>
                <TableHead>模型</TableHead>
                <TableHead>端点</TableHead>
                <TableHead>分类</TableHead>
                <TableHead>错误</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(metrics?.recent_failures || []).slice(0, 10).map((row, index) => (
                <TableRow key={`${row.ts}-${index}`}>
                  <TableCell className="whitespace-nowrap text-xs">
                    {new Date(row.ts * 1000).toLocaleString()}
                  </TableCell>
                  <TableCell className="font-mono text-xs">{row.model}</TableCell>
                  <TableCell className="text-xs">{row.endpoint}</TableCell>
                  <TableCell className="text-xs">{row.error_category || "-"}</TableCell>
                  <TableCell className="max-w-[360px] truncate text-xs text-muted-foreground" title={row.error}>
                    {row.error || "-"}
                  </TableCell>
                </TableRow>
              ))}
              {!metrics?.recent_failures?.length && (
                <TableRow>
                  <TableCell colSpan={5} className="text-center text-sm text-muted-foreground">
                    暂无失败记录
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}

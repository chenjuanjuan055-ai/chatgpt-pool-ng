"use client";

import { useEffect, useState } from "react";
import { FileText, LoaderCircle, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { fetchMinimaxAudits } from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";

export default function MinimaxAuditsPage() {
  const { isCheckingAuth } = useAuthGuard(["admin"]);
  const [items, setItems] = useState<Array<Record<string, any>>>([]);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = async (target = page) => {
    setLoading(true);
    try {
      const data = await fetchMinimaxAudits(target, 20);
      setItems(data.items || []);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "读取失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!isCheckingAuth) void load(page);
  }, [isCheckingAuth, page]);

  return (
    <div className="space-y-5 pt-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-stone-900">MiniMax 请求审计</h1>
          <p className="mt-1 text-sm text-stone-500">网关侧记录的每次调用</p>
        </div>
        <Button variant="outline" onClick={() => void load(page)} disabled={loading}>
          {loading ? <LoaderCircle className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
          刷新
        </Button>
      </div>

      {error ? (
        <Card className="rounded-2xl border-rose-200 bg-rose-50/70">
          <CardContent className="p-4 text-sm text-rose-700">{error}</CardContent>
        </Card>
      ) : null}

      <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
        <CardContent className="p-0">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="bg-stone-50/80 text-xs text-stone-400">
                <tr>
                  <th className="px-4 py-3 font-normal">时间</th>
                  <th className="px-4 py-3 font-normal">模型</th>
                  <th className="px-4 py-3 font-normal">密钥</th>
                  <th className="px-4 py-3 font-normal">账号</th>
                  <th className="px-4 py-3 font-normal">状态</th>
                  <th className="px-4 py-3 font-normal">延迟</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-stone-100">
                {items.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="px-4 py-10 text-center text-sm text-stone-400">
                      {loading ? "加载中…" : "暂无审计记录"}
                    </td>
                  </tr>
                ) : (
                  items.map((row) => (
                    <tr key={row.id} className="text-stone-600">
                      <td className="px-4 py-3 text-xs">{String(row.createdAt || "").slice(0, 19).replace("T", " ")}</td>
                      <td className="px-4 py-3 text-xs">{row.model || "-"}</td>
                      <td className="px-4 py-3 text-xs">{row.keyName || "-"}</td>
                      <td className="px-4 py-3 text-xs">{row.accountName || row.account || "-"}</td>
                      <td className="px-4 py-3">
                        <span className={Number(row.status) >= 400 ? "text-rose-500" : "text-emerald-600"}>
                          {row.status ?? "-"}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-xs">{row.latencyMs ?? "-"} ms</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      <div className="flex justify-end gap-2">
        <Button size="sm" variant="outline" disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>上一页</Button>
        <Button size="sm" variant="outline" disabled={items.length < 20} onClick={() => setPage((p) => p + 1)}>下一页</Button>
      </div>
    </div>
  );
}

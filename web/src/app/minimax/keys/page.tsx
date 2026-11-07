"use client";

import { useEffect, useState } from "react";
import { KeyRound, LoaderCircle, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { fetchMinimaxKeys } from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";

export default function MinimaxKeysPage() {
  const { isCheckingAuth } = useAuthGuard(["admin"]);
  const [items, setItems] = useState<Array<Record<string, any>>>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = async () => {
    setLoading(true);
    try {
      const data = await fetchMinimaxKeys();
      setItems(data.items || []);
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

  return (
    <div className="space-y-5 pt-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-stone-900">MiniMax 客户端密钥</h1>
          <p className="mt-1 text-sm text-stone-500">调用网关用的 Bearer 凭据，共 {items.length} 个</p>
        </div>
        <Button variant="outline" onClick={() => void load()} disabled={loading}>
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
                  <th className="px-4 py-3 font-normal">名称</th>
                  <th className="px-4 py-3 font-normal">密钥</th>
                  <th className="px-4 py-3 font-normal">状态</th>
                  <th className="px-4 py-3 font-normal">RPM 上限</th>
                  <th className="px-4 py-3 font-normal">并发</th>
                  <th className="px-4 py-3 font-normal">累计请求</th>
                  <th className="px-4 py-3 font-normal">最近使用</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-stone-100">
                {items.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="px-4 py-10 text-center text-sm text-stone-400">
                      {loading ? "加载中…" : "暂无密钥"}
                    </td>
                  </tr>
                ) : (
                  items.map((key) => (
                    <tr key={key.id} className="text-stone-600">
                      <td className="px-4 py-3">
                        <div className="inline-flex items-center gap-1.5 font-medium text-stone-800">
                          <KeyRound className="size-3.5" />
                          {key.name || key.id}
                        </div>
                      </td>
                      <td className="px-4 py-3 font-mono text-xs">{String(key.key || "").slice(0, 12)}…</td>
                      <td className="px-4 py-3 text-xs">
                        <span className={key.enabled ? "text-emerald-600" : "text-stone-400"}>
                          {key.enabled ? "启用" : "停用"}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-xs">{key.rpmLimit ?? 0}</td>
                      <td className="px-4 py-3 text-xs">{key.maxConcurrent ?? "-"}</td>
                      <td className="px-4 py-3 text-xs">{key.totalRequests ?? 0}</td>
                      <td className="px-4 py-3 text-xs">{String(key.lastUsedAt || "").slice(0, 19).replace("T", " ") || "-"}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

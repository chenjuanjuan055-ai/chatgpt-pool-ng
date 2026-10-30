"use client";

import { useEffect, useState } from "react";
import { LoaderCircle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { fetchGrokAudits, type GrokAudit } from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";

export default function GrokAuditsPage() {
  const { isCheckingAuth } = useAuthGuard(["admin"]);
  const [items, setItems] = useState<GrokAudit[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    if (isCheckingAuth) return;
    void (async () => {
      try {
        const data = await fetchGrokAudits(1, 50);
        setItems(data.items || []);
      } catch (err) {
        setError(err instanceof Error ? err.message : "读取失败");
      } finally {
        setLoading(false);
      }
    })();
  }, [isCheckingAuth]);

  return (
    <div className="space-y-4 pt-6">
      <div>
        <p className="text-[11px] font-medium uppercase tracking-[0.28em] text-stone-400">Grok</p>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight text-stone-900">请求日志</h1>
        <p className="mt-1 text-sm text-stone-500">最近 50 条调用记录。</p>
      </div>

      <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
        <CardContent className="p-0">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[900px] text-left text-sm">
              <thead className="border-b border-stone-100 text-[11px] uppercase tracking-[0.18em] text-stone-400">
                <tr>
                  <th className="px-4 py-3">时间</th>
                  <th className="px-4 py-3">模型</th>
                  <th className="px-4 py-3">来源</th>
                  <th className="px-4 py-3">密钥</th>
                  <th className="px-4 py-3">状态</th>
                  <th className="px-4 py-3">耗时</th>
                </tr>
              </thead>
              <tbody>
                {loading ? (
                  <tr>
                    <td colSpan={6} className="px-4 py-10 text-center">
                      <LoaderCircle className="mx-auto size-4 animate-spin text-stone-400" />
                    </td>
                  </tr>
                ) : items.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="px-4 py-10 text-center text-stone-400">{error || "暂无记录"}</td>
                  </tr>
                ) : (
                  items.map((item) => (
                    <tr key={item.id} className="border-b border-stone-100/80 text-stone-600 hover:bg-stone-50/70">
                      <td className="px-4 py-3 text-xs text-stone-500">{item.createdAt ? item.createdAt.slice(0, 19).replace("T", " ") : "—"}</td>
                      <td className="px-4 py-3 font-medium text-stone-700">{item.modelPublicId || "—"}</td>
                      <td className="px-4 py-3">
                        <Badge variant="outline" className="rounded-md border-stone-200 text-stone-600">
                          {item.provider || "—"}
                        </Badge>
                      </td>
                      <td className="px-4 py-3 text-xs">{item.clientKeyName || "—"}</td>
                      <td className="px-4 py-3 text-xs">{item.status || "—"}</td>
                      <td className="px-4 py-3 text-xs">{item.durationMs != null ? `${item.durationMs} ms` : "—"}</td>
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

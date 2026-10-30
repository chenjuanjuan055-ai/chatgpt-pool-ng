"use client";

import { useEffect, useState } from "react";
import { LoaderCircle } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { fetchGrokModelRoutes, grokUpdateModelRoute, type GrokModelRoute } from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";

const PROVIDER_LABEL: Record<string, string> = {
  grok_web: "Web",
  grok_build: "Build",
  grok_console: "Console",
};

export default function GrokRoutesPage() {
  const { isCheckingAuth } = useAuthGuard(["admin"]);
  const [items, setItems] = useState<GrokModelRoute[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    if (isCheckingAuth) return;
    void (async () => {
      try {
        const data = await fetchGrokModelRoutes();
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
        <h1 className="mt-1 text-2xl font-semibold tracking-tight text-stone-900">模型路由</h1>
        <p className="mt-1 text-sm text-stone-500">对外模型名 → 上游来源与真实模型的映射，共 {items.length} 条。</p>
      </div>

      <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
        <CardContent className="p-0">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[860px] text-left text-sm">
              <thead className="border-b border-stone-100 text-[11px] uppercase tracking-[0.18em] text-stone-400">
                <tr>
                  <th className="px-4 py-3">对外模型</th>
                  <th className="px-4 py-3">来源</th>
                  <th className="px-4 py-3">上游模型</th>
                  <th className="px-4 py-3">能力</th>
                  <th className="px-4 py-3">状态</th>
                  <th className="px-4 py-3">操作</th>
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
                    <td colSpan={6} className="px-4 py-10 text-center text-stone-400">{error || "暂无路由"}</td>
                  </tr>
                ) : (
                  items.map((item) => (
                    <tr key={item.id} className="border-b border-stone-100/80 text-stone-600 hover:bg-stone-50/70">
                      <td className="px-4 py-3 font-medium text-stone-700">{item.publicId}</td>
                      <td className="px-4 py-3">
                        <Badge variant="outline" className="rounded-md border-stone-200 text-stone-600">
                          {PROVIDER_LABEL[item.provider] || item.provider}
                        </Badge>
                      </td>
                      <td className="px-4 py-3 font-mono text-xs text-stone-500">{item.upstreamModel}</td>
                      <td className="px-4 py-3 text-xs">{item.capability || "—"}</td>
                      <td className="px-4 py-3">
                        <Badge
                          variant="outline"
                          className={`rounded-md ${item.enabled ? "border-emerald-200 text-emerald-600" : "border-stone-200 text-stone-500"}`}
                        >
                          {item.enabled ? "启用" : "停用"}
                        </Badge>
                      </td>
                      <td className="px-4 py-3">
                        <Button
                          variant="ghost"
                          className="h-8 rounded-lg px-2 text-xs"
                          onClick={async () => {
                            try {
                              await grokUpdateModelRoute(item.id, { enabled: !item.enabled });
                              toast.success(item.enabled ? "已停用路由" : "已启用路由");
                              const data = await fetchGrokModelRoutes();
                              setItems(data.items || []);
                            } catch (err) {
                              toast.error(err instanceof Error ? err.message : "更新失败");
                            }
                          }}
                        >
                          {item.enabled ? "停用" : "启用"}
                        </Button>
                      </td>
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

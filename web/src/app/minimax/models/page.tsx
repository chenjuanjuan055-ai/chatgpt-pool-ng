"use client";

import { useEffect, useState } from "react";
import { Layers, LoaderCircle, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { fetchMinimaxModels } from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";

export default function MinimaxModelsPage() {
  const { isCheckingAuth } = useAuthGuard(["admin"]);
  const [items, setItems] = useState<Array<Record<string, any>>>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = async () => {
    setLoading(true);
    try {
      const data = await fetchMinimaxModels();
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
          <h1 className="text-2xl font-semibold tracking-tight text-stone-900">MiniMax 模型目录</h1>
          <p className="mt-1 text-sm text-stone-500">网关对外暴露的模型，共 {items.length} 个</p>
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

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {items.length === 0 && !loading ? (
          <p className="text-sm text-stone-400">暂无模型</p>
        ) : (
          items.map((model) => (
            <Card key={model.id} className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
              <CardContent className="space-y-2 p-5">
                <div className="flex items-center justify-between">
                  <span className="inline-flex items-center gap-1.5 text-sm font-semibold text-stone-900">
                    <Layers className="size-4" />
                    {model.id}
                  </span>
                  <span className={model.enabled ? "text-xs text-emerald-600" : "text-xs text-stone-400"}>
                    {model.enabled ? "启用" : "停用"}
                  </span>
                </div>
                <p className="text-xs text-stone-500">{model.name || model.upstream || "-"}</p>
                <div className="flex flex-wrap gap-3 text-[11px] text-stone-400">
                  {model.upstream ? <span>上游 {model.upstream}</span> : null}
                  {model.upstreamModel ? <span>映射 {model.upstreamModel}</span> : null}
                  {model.contextWindow ? <span>上下文 {model.contextWindow}</span> : null}
                </div>
              </CardContent>
            </Card>
          ))
        )}
      </div>
    </div>
  );
}

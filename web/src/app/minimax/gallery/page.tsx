"use client";

import { useEffect, useState } from "react";
import { Image as ImageIcon, LoaderCircle, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { fetchMinimaxGallery } from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";

export default function MinimaxGalleryPage() {
  const { isCheckingAuth } = useAuthGuard(["admin"]);
  const [items, setItems] = useState<Array<Record<string, any>>>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = async () => {
    setLoading(true);
    try {
      const data = await fetchMinimaxGallery();
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
          <h1 className="text-2xl font-semibold tracking-tight text-stone-900">MiniMax 图库</h1>
          <p className="mt-1 text-sm text-stone-500">网关侧产出的媒体，共 {items.length} 项</p>
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

      {items.length === 0 && !loading ? (
        <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
          <CardContent className="p-10 text-center text-sm text-stone-400">暂无媒体</CardContent>
        </Card>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {items.map((item) => (
            <Card key={item.id} className="overflow-hidden rounded-2xl border-white/80 bg-white/90 shadow-sm">
              <div className="aspect-square w-full bg-stone-100">
                {item.url ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={String(item.url)} alt={String(item.prompt || item.id)} className="size-full object-cover" />
                ) : (
                  <div className="flex size-full items-center justify-center text-stone-300">
                    <ImageIcon className="size-8" />
                  </div>
                )}
              </div>
              <CardContent className="space-y-1 p-4">
                <div className="truncate text-sm text-stone-700">{String(item.prompt || item.id)}</div>
                <div className="flex items-center justify-between text-xs text-stone-400">
                  <span>{item.kind || "media"}</span>
                  <span>{String(item.createdAt || "").slice(0, 10)}</span>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

"use client";

import { useEffect, useState } from "react";
import { LoaderCircle } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import { fetchGrokMedia, type GrokMedia } from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";

export default function GrokMediaPage() {
  const { isCheckingAuth } = useAuthGuard(["admin"]);
  const [items, setItems] = useState<GrokMedia[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    if (isCheckingAuth) return;
    void (async () => {
      try {
        const data = await fetchGrokMedia();
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
        <h1 className="mt-1 text-2xl font-semibold tracking-tight text-stone-900">图库</h1>
        <p className="mt-1 text-sm text-stone-500">上游生成的图片资产，共 {items.length} 个。</p>
      </div>
      <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
        <CardContent className="p-5">
          {loading ? (
            <LoaderCircle className="size-4 animate-spin text-stone-400" />
          ) : items.length === 0 ? (
            <p className="text-sm text-stone-400">{error || "暂无图片"}</p>
          ) : (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-6">
              {items.map((item) => (
                <a key={item.id} href={item.url} target="_blank" rel="noreferrer" className="group overflow-hidden rounded-xl border border-stone-200">
                  {item.url ? (
                    <img src={item.url} alt={item.id} className="h-32 w-full object-cover" loading="lazy" />
                  ) : (
                    <div className="flex h-32 items-center justify-center bg-stone-50 text-[11px] text-stone-400">
                      {item.kind || "image"} · {Math.round((item.sizeBytes || 0) / 1024)} KB
                    </div>
                  )}
                  <div className="truncate px-2 py-1 text-[11px] text-stone-500">{item.id}</div>
                </a>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

"use client";

import { useEffect, useState } from "react";
import { LoaderCircle } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import { fetchGrokVideos } from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";

type Video = { id: string; model?: string; prompt?: string; status?: string; url?: string };

export default function GrokVideosPage() {
  const { isCheckingAuth } = useAuthGuard(["admin"]);
  const [items, setItems] = useState<Video[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    if (isCheckingAuth) return;
    void (async () => {
      try {
        const data = await fetchGrokVideos(1, 24);
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
        <h1 className="mt-1 text-2xl font-semibold tracking-tight text-stone-900">视频库</h1>
        <p className="mt-1 text-sm text-stone-500">上游生成的视频资产，共 {items.length} 个。</p>
      </div>
      <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
        <CardContent className="p-5">
          {loading ? (
            <LoaderCircle className="size-4 animate-spin text-stone-400" />
          ) : items.length === 0 ? (
            <p className="text-sm text-stone-400">{error || "暂无视频"}</p>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {items.map((item) => (
                <a key={item.id} href={item.url || "#"} target="_blank" rel="noreferrer" className="block rounded-xl border border-stone-200 p-3 transition hover:border-indigo-200">
                  <div className="flex items-center justify-between text-xs text-stone-500">
                    <span className="font-medium text-stone-700">{item.model || "video"}</span>
                    <span>{item.status || "—"}</span>
                  </div>
                  <p className="mt-1 line-clamp-2 text-xs text-stone-400">{item.prompt || item.id}</p>
                </a>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

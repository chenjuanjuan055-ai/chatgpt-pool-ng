"use client";

import { useEffect, useRef, useState } from "react";
import { Activity, LoaderCircle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { fetchGrokEvents, type GrokEvent } from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";

const KIND_LABEL: Record<string, string> = {
  account: "账号",
  batch: "批量",
  error: "异常",
};

export default function GrokLivePage() {
  const { isCheckingAuth } = useAuthGuard(["admin"]);
  const [items, setItems] = useState<GrokEvent[]>([]);
  const [error, setError] = useState("");
  const lastTs = useRef(0);

  useEffect(() => {
    if (isCheckingAuth) return;
    let timer: ReturnType<typeof setInterval> | null = null;
    const tick = async () => {
      try {
        const data = await fetchGrokEvents(lastTs.current);
        lastTs.current = data.now;
        if (data.items?.length) {
          setItems((prev) => [...data.items, ...prev].slice(0, 200));
        }
        setError("");
      } catch (err) {
        setError(err instanceof Error ? err.message : "读取失败");
      }
    };
    void tick();
    timer = setInterval(() => void tick(), 3000);
    return () => {
      if (timer) clearInterval(timer);
    };
  }, [isCheckingAuth]);

  return (
    <div className="space-y-4 pt-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-[11px] font-medium uppercase tracking-[0.28em] text-stone-400">Grok</p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-stone-900">实时控制台</h1>
          <p className="mt-1 text-sm text-stone-500">账号操作实时流水，每 3 秒拉取一次。</p>
        </div>
        <span className="flex items-center gap-2 text-xs text-emerald-600">
          <Activity className="size-4" /> 运行中
        </span>
      </div>

      <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
        <CardContent className="p-0">
          <div className="max-h-[600px] divide-y divide-stone-100 overflow-y-auto">
            {items.length === 0 ? (
              <div className="px-5 py-12 text-center text-sm text-stone-400">
                {error || "暂无事件——去账号页做一次操作，这里会实时出现"}
              </div>
            ) : (
              items.map((item, index) => (
                <div key={`${item.ts}-${index}`} className="flex items-start gap-3 px-5 py-2.5 text-sm">
                  <span className="w-20 shrink-0 font-mono text-xs text-stone-400">
                    {new Date(item.ts * 1000).toLocaleTimeString("zh-CN", { hour12: false })}
                  </span>
                  <Badge variant="outline" className={`shrink-0 rounded-md ${item.kind === "error" ? "border-rose-200 text-rose-500" : "border-stone-200 text-stone-600"}`}>
                    {KIND_LABEL[item.kind] || item.kind}
                  </Badge>
                  <div className="min-w-0">
                    <p className="text-stone-700">{item.message}</p>
                    {item.detail ? <p className="truncate text-xs text-stone-400">{item.detail}</p> : null}
                  </div>
                </div>
              ))
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

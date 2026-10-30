"use client";

import { useEffect, useState } from "react";
import { LoaderCircle, ShieldCheck, ShieldOff } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import { fetchGrokGuard } from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";

export default function GrokGuardPage() {
  const { isCheckingAuth } = useAuthGuard(["admin"]);
  const [data, setData] = useState<{ available?: boolean; [key: string]: unknown } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    if (isCheckingAuth) return;
    void (async () => {
      try {
        setData(await fetchGrokGuard());
      } catch (err) {
        setError(err instanceof Error ? err.message : "读取失败");
      } finally {
        setLoading(false);
      }
    })();
  }, [isCheckingAuth]);

  const available = Boolean(data?.available);

  return (
    <div className="space-y-4 pt-6">
      <div>
        <p className="text-[11px] font-medium uppercase tracking-[0.28em] text-stone-400">Grok</p>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight text-stone-900">质量守护</h1>
        <p className="mt-1 text-sm text-stone-500">出口质量守卫：按节点成功率自动降级。</p>
      </div>
      <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
        <CardContent className="flex items-center gap-3 p-6">
          {loading ? (
            <LoaderCircle className="size-4 animate-spin text-stone-400" />
          ) : available ? (
            <>
              <ShieldCheck className="size-5 text-emerald-500" />
              <div>
                <p className="text-sm font-medium text-stone-800">守护已启用</p>
                <p className="text-xs text-stone-400">上游已开启出口质量守卫</p>
              </div>
            </>
          ) : (
            <>
              <ShieldOff className="size-5 text-stone-400" />
              <div>
                <p className="text-sm font-medium text-stone-800">未启用</p>
                <p className="text-xs text-stone-400">{error || "上游当前未开启出口质量守护，开启后这里会显示策略与节点评分。"}</p>
              </div>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

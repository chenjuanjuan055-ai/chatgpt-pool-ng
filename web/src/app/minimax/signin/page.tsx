"use client";

import { useEffect, useState } from "react";
import { LoaderCircle, Play, RefreshCw, UserPlus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { fetchMinimaxSignin } from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";

export default function MinimaxSigninPage() {
  const { isCheckingAuth } = useAuthGuard(["admin"]);
  const [data, setData] = useState<Record<string, any> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = async () => {
    setLoading(true);
    try {
      setData(await fetchMinimaxSignin());
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

  const last = data?.lastReport || {};

  return (
    <div className="space-y-5 pt-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-stone-900">MiniMax 签到</h1>
          <p className="mt-1 text-sm text-stone-500">
            自动签到 · {data?.enabled ? "已启用" : "未启用"}
          </p>
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
        <CardContent className="space-y-3 p-5">
          <div className="flex items-center gap-2 text-sm font-semibold text-stone-900">
            <UserPlus className="size-4" />
            上次签到报告
          </div>
          {Object.keys(last).length === 0 ? (
            <p className="text-sm text-stone-400">暂无签到记录</p>
          ) : (
            <div className="grid gap-3 text-sm sm:grid-cols-2">
              {Object.entries(last).map(([key, value]) => (
                <div key={key} className="rounded-xl border border-stone-100 bg-stone-50/60 px-3 py-2">
                  <div className="text-xs text-stone-400">{key}</div>
                  <div className="mt-0.5 break-all text-stone-700">
                    {typeof value === "object" ? JSON.stringify(value) : String(value)}
                  </div>
                </div>
              ))}
            </div>
          )}
          <div className="flex items-center gap-2 pt-1">
            <Button size="sm" variant="outline" disabled>
              <Play className="size-4" />
              触发签到（在 MiniMax 控制台执行）
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

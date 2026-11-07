"use client";

import { useEffect, useRef, useState } from "react";
import { CircleAlert, LoaderCircle, LogIn, RefreshCw, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  minimaxBrowserLoginCancel,
  minimaxBrowserLoginPoll,
  minimaxBrowserLoginStart,
} from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";

type Session = { sessionId: string; target?: string; region?: string; proxy?: string };
type Result = { status: "pending" | "ok" | "timeout" | "error"; account?: any; error?: string };

export default function MinimaxBrowserLoginPage() {
  const { isCheckingAuth } = useAuthGuard(["admin"]);
  const [region, setRegion] = useState("global");
  const [proxy, setProxy] = useState("");
  const [session, setSession] = useState<Session | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = () => {
    if (timer.current) {
      clearInterval(timer.current);
      timer.current = null;
    }
  };

  useEffect(() => stopPolling, []);

  const start = async () => {
    setBusy(true);
    setError("");
    setResult(null);
    try {
      const data = await minimaxBrowserLoginStart({ region, ...(proxy ? { proxy } : {}) });
      setSession(data);
      stopPolling();
      timer.current = setInterval(() => {
        void (async () => {
          try {
            const poll = await minimaxBrowserLoginPoll(data.sessionId);
            setResult(poll);
            if (poll.status !== "pending") {
              stopPolling();
            }
          } catch (err) {
            setError(err instanceof Error ? err.message : "轮询失败");
            stopPolling();
          }
        })();
      }, 3000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "启动浏览器登录失败");
    } finally {
      setBusy(false);
    }
  };

  const cancel = async () => {
    if (!session) return;
    try {
      await minimaxBrowserLoginCancel(session.sessionId);
    } catch {
      /* 已结束就算了 */
    }
    stopPolling();
    setSession(null);
    setResult(null);
  };

  return (
    <div className="space-y-5 pt-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-stone-900">浏览器登录</h1>
        <p className="mt-1 text-sm text-stone-500">
          在托管浏览器里打开上游登录页手动完成验证，成功后账号会写回 minimax2api 的账号池。
        </p>
      </div>

      {error ? (
        <Card className="rounded-2xl border-rose-200 bg-rose-50/70">
          <CardContent className="flex items-start gap-2 p-4 text-sm text-rose-700">
            <CircleAlert className="mt-0.5 size-4 shrink-0" />
            <span>{error}</span>
          </CardContent>
        </Card>
      ) : null}

      <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
        <CardContent className="space-y-4 p-5">
          <div className="grid gap-3 md:grid-cols-2">
            <div className="space-y-1.5">
              <div className="text-xs text-stone-400">区域</div>
              <Select value={region} onValueChange={setRegion}>
                <SelectTrigger className="h-9"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="global">global（国际站）</SelectItem>
                  <SelectItem value="cn">cn（国内站）</SelectItem>
                  <SelectItem value="auto">auto</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <div className="text-xs text-stone-400">出站代理（可选，国际站有地域限制时填）</div>
              <Input value={proxy} onChange={(e) => setProxy(e.target.value)} placeholder="http://127.0.0.1:7953" />
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button onClick={() => void start()} disabled={busy || Boolean(session)}>
              {busy ? <LoaderCircle className="size-4 animate-spin" /> : <LogIn className="size-4" />}
              启动浏览器登录
            </Button>
            {session ? (
              <Button variant="outline" onClick={() => void cancel()}>
                <X className="size-4" />取消
              </Button>
            ) : null}
            {session ? (
              <Button variant="ghost" onClick={() => void start()} disabled={busy}>
                <RefreshCw className="size-4" />重新开始
              </Button>
            ) : null}
          </div>
        </CardContent>
      </Card>

      {session ? (
        <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
          <CardContent className="space-y-3 p-5">
            <div className="text-sm font-semibold text-stone-900">会话状态</div>
            <div className="grid gap-2 text-sm sm:grid-cols-2">
              <div className="rounded-xl border border-stone-100 bg-stone-50/60 px-3 py-2">
                <div className="text-xs text-stone-400">会话 ID</div>
                <div className="mt-0.5 break-all font-mono text-xs">{session.sessionId}</div>
              </div>
              <div className="rounded-xl border border-stone-100 bg-stone-50/60 px-3 py-2">
                <div className="text-xs text-stone-400">状态</div>
                <div className="mt-0.5">
                  {result?.status === "ok" ? <span className="text-emerald-600">成功</span> : null}
                  {result?.status === "pending" ? <span className="text-amber-500">等待中</span> : null}
                  {result?.status === "timeout" ? <span className="text-rose-500">超时</span> : null}
                  {result?.status === "error" ? <span className="text-rose-500">失败</span> : null}
                  {!result ? <span className="text-stone-400">未开始</span> : null}
                </div>
              </div>
              {session.target ? (
                <div className="rounded-xl border border-stone-100 bg-stone-50/60 px-3 py-2 sm:col-span-2">
                  <div className="text-xs text-stone-400">目标地址</div>
                  <div className="mt-0.5 break-all font-mono text-xs">{session.target}</div>
                </div>
              ) : null}
              {result?.account ? (
                <div className="rounded-xl border border-stone-100 bg-stone-50/60 px-3 py-2 sm:col-span-2">
                  <div className="text-xs text-stone-400">写入的账号</div>
                  <div className="mt-0.5 text-xs">
                    {result.account.name || result.account.id} · {result.account.identifier || "-"}
                  </div>
                </div>
              ) : null}
              {result?.error ? (
                <div className="rounded-xl border border-rose-100 bg-rose-50/60 px-3 py-2 text-xs text-rose-700 sm:col-span-2">
                  {result.error}
                </div>
              ) : null}
            </div>
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}

"use client";

import { useEffect, useState } from "react";
import { LoaderCircle, RefreshCw, Search, Users } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  fetchMinimaxAccounts,
  minimaxAccountAction,
  minimaxAccountBatch,
  type MinimaxAccount,
} from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";

const STATUS_STYLE: Record<string, string> = {
  active: "text-emerald-600",
  ready: "text-emerald-600",
  disabled: "text-stone-400",
  cooldown: "text-amber-500",
  invalid: "text-rose-500",
  error: "text-rose-500",
};

export default function MinimaxAccountsPage() {
  const { isCheckingAuth } = useAuthGuard(["admin"]);
  const [items, setItems] = useState<MinimaxAccount[]>([]);
  const [summary, setSummary] = useState<Record<string, any>>({});
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = async (target = page, keyword = q) => {
    setLoading(true);
    try {
      const data = await fetchMinimaxAccounts(target, 20, keyword);
      setItems(data.items || []);
      setTotal(Number(data.total || 0));
      setSummary((data as any).summary || {});
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "读取失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!isCheckingAuth) void load(page, q);
  }, [isCheckingAuth, page]);

  const toggle = (id: string) => {
    setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  };

  const runBatch = async (action: string) => {
    if (selected.length === 0) return;
    try {
      await minimaxAccountBatch(action, selected);
      setSelected([]);
      await load(page, q);
    } catch (err) {
      setError(err instanceof Error ? err.message : "操作失败");
    }
  };

  const runOne = async (accountId: string, action: string) => {
    try {
      await minimaxAccountAction(accountId, action);
      await load(page, q);
    } catch (err) {
      setError(err instanceof Error ? err.message : "操作失败");
    }
  };

  const pages = Math.max(1, Math.ceil(total / 20));

  return (
    <div className="space-y-5 pt-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-stone-900">MiniMax 账号池</h1>
          <p className="mt-1 text-sm text-stone-500">
            共 {total} 个账号 · 选中 {selected.length} 个
            {summary?.active ? ` · 活跃 ${summary.active}` : ""}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <form
            onSubmit={(event) => {
              event.preventDefault();
              setPage(1);
              void load(1, q);
            }}
            className="flex items-center gap-2"
          >
            <Input
              value={q}
              onChange={(event) => setQ(event.target.value)}
              placeholder="搜索账号名 / 标识"
              className="h-9 w-52"
            />
            <Button type="submit" variant="outline" size="sm">
              <Search className="size-4" />
              搜索
            </Button>
          </form>
          <Button variant="outline" size="sm" onClick={() => void load(page, q)} disabled={loading}>
            {loading ? <LoaderCircle className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
            刷新
          </Button>
        </div>
      </div>

      {selected.length > 0 ? (
        <div className="flex flex-wrap items-center gap-2 rounded-2xl border border-emerald-200 bg-emerald-50/70 px-4 py-3">
          <span className="text-sm text-emerald-800">已选 {selected.length} 个</span>
          <Button size="sm" variant="outline" onClick={() => void runBatch("enable")}>启用</Button>
          <Button size="sm" variant="outline" onClick={() => void runBatch("disable")}>停用</Button>
          <Button size="sm" variant="outline" onClick={() => void runBatch("clearCooldown")}>清除冷却</Button>
          <Button size="sm" variant="outline" onClick={() => void runBatch("quota")}>刷新额度</Button>
          <Button size="sm" variant="destructive" onClick={() => void runBatch("delete")}>删除</Button>
        </div>
      ) : null}

      {error ? (
        <Card className="rounded-2xl border-rose-200 bg-rose-50/70">
          <CardContent className="p-4 text-sm text-rose-700">{error}</CardContent>
        </Card>
      ) : null}

      <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
        <CardContent className="p-0">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="bg-stone-50/80 text-xs text-stone-400">
                <tr>
                  <th className="w-10 px-4 py-3 font-normal"></th>
                  <th className="px-4 py-3 font-normal">账号</th>
                  <th className="px-4 py-3 font-normal">类型</th>
                  <th className="px-4 py-3 font-normal">区域</th>
                  <th className="px-4 py-3 font-normal">状态</th>
                  <th className="px-4 py-3 font-normal">并发</th>
                  <th className="px-4 py-3 font-normal">最近使用</th>
                  <th className="px-4 py-3 font-normal">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-stone-100">
                {items.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="px-4 py-10 text-center text-sm text-stone-400">
                      {loading ? "加载中…" : "暂无账号"}
                    </td>
                  </tr>
                ) : (
                  items.map((account) => {
                    const status = String(account.status || "");
                    return (
                      <tr key={account.id} className="text-stone-600">
                        <td className="px-4 py-3">
                          <input
                            type="checkbox"
                            checked={selected.includes(account.id)}
                            onChange={() => toggle(account.id)}
                            aria-label={`选择 ${account.name || account.id}`}
                          />
                        </td>
                        <td className="px-4 py-3">
                          <div className="font-medium text-stone-800">{account.name || account.id}</div>
                          <div className="text-xs text-stone-400">{account.identifier || account.userId || ""}</div>
                        </td>
                        <td className="px-4 py-3 text-xs">{account.kind || "-"}</td>
                        <td className="px-4 py-3 text-xs">{account.region || "-"}</td>
                        <td className={`px-4 py-3 text-xs ${STATUS_STYLE[status] || "text-stone-500"}`}>{status || "-"}</td>
                        <td className="px-4 py-3 text-xs">{account.maxConcurrent ?? "-"}</td>
                        <td className="px-4 py-3 text-xs">{String(account.lastUsedAt || "").slice(0, 19).replace("T", " ") || "-"}</td>
                        <td className="px-4 py-3">
                          <div className="flex gap-2">
                            <Button size="sm" variant="ghost" onClick={() => void runOne(account.id, "probe")}>探活</Button>
                            <Button size="sm" variant="ghost" onClick={() => void runOne(account.id, "quota")}>额度</Button>
                            <Button size="sm" variant="ghost" onClick={() => void runOne(account.id, "signin")}>签到</Button>
                          </div>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      <div className="flex items-center justify-between text-sm text-stone-500">
        <span className="inline-flex items-center gap-1.5"><Users className="size-4" />第 {page} / {pages} 页</span>
        <div className="flex gap-2">
          <Button size="sm" variant="outline" disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>上一页</Button>
          <Button size="sm" variant="outline" disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>下一页</Button>
        </div>
      </div>
    </div>
  );
}

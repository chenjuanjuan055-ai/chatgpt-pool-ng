"use client";

import { useEffect, useState } from "react";
import { LoaderCircle, Download, Plus, RefreshCw, Search, Upload, Users } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import {
  fetchMinimaxAccountGroups,
  fetchMinimaxAccounts,
  minimaxAccountAction,
  minimaxAccountBatch,
  minimaxAccountCreate,
  minimaxAccountDelete,
  minimaxAccountUpdate,
  minimaxAccountsExport,
  minimaxAccountsImport,
  minimaxCleanup,
  minimaxProbeAll,
  minimaxQuotaAll,
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
  const [busy, setBusy] = useState("");
  const [importText, setImportText] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [kindFilter, setKindFilter] = useState("");
  const [groupFilter, setGroupFilter] = useState("");
  const [sortBy, setSortBy] = useState("");
  const [sortOrder, setSortOrder] = useState("");
  const [groups, setGroups] = useState<string[]>([]);
  const [editing, setEditing] = useState<MinimaxAccount | null>(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ name: "", kind: "token", region: "global", group: "", remark: "", priority: "50", maxConcurrent: "2" });

  const load = async (target = page, keyword = q) => {
    setLoading(true);
    try {
      const data = await fetchMinimaxAccounts(target, 20, keyword, {
        status: statusFilter,
        kind: kindFilter,
        group: groupFilter,
        sortBy,
        sortOrder,
      });
      setItems(data.items || []);
      setTotal(Number(data.total || 0));
      setSummary(data.summary || {});
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "读取失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (isCheckingAuth) return;
    void (async () => {
      try {
        const data = await fetchMinimaxAccountGroups();
        setGroups(data.groups || []);
      } catch {
        setGroups([]);
      }
    })();
  }, [isCheckingAuth]);

  const runJob = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label);
    setError("");
    try {
      await fn();
      await load(page, q);
    } catch (err) {
      setError(err instanceof Error ? err.message : `${label} 失败`);
    } finally {
      setBusy("");
    }
  };

  const exportAccounts = () =>
    runJob("导出", async () => {
      const data = await minimaxAccountsExport("json", 500);
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `minimax-accounts-${Date.now()}.json`;
      link.click();
      URL.revokeObjectURL(url);
    });

  const importAccounts = () =>
    runJob("导入", async () => {
      if (!importText.trim()) throw new Error("请先粘贴账号数据");
      await minimaxAccountsImport({ data: importText.trim() });
      setImportText("");
    });

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

  const saveAccount = async () => {
    setBusy("保存账号");
    try {
      const payload = {
        name: form.name.trim(),
        kind: form.kind,
        region: form.region,
        group: form.group.trim(),
        remark: form.remark.trim(),
        priority: Number(form.priority) || 0,
        maxConcurrent: Number(form.maxConcurrent) || 1,
      };
      if (editing) {
        await minimaxAccountUpdate(String(editing.id), payload);
      } else {
        await minimaxAccountCreate(payload);
      }
      setEditing(null);
      setCreating(false);
      await load(page, q);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setBusy("");
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

      <div className="flex flex-wrap items-end gap-2">
        <div className="space-y-1.5">
          <div className="text-xs text-stone-400">状态</div>
          <Select value={statusFilter} onValueChange={(v) => { setStatusFilter(v); setPage(1); }}>
            <SelectTrigger className="h-9 w-32"><SelectValue placeholder="全部" /></SelectTrigger>
            <SelectContent>
              {["", "active", "ready", "disabled", "cooldown", "invalid", "error"].map((s) => (
                <SelectItem key={s || "all"} value={s}>{s || "全部"}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1.5">
          <div className="text-xs text-stone-400">类型</div>
          <Select value={kindFilter} onValueChange={(v) => { setKindFilter(v); setPage(1); }}>
            <SelectTrigger className="h-9 w-32"><SelectValue placeholder="全部" /></SelectTrigger>
            <SelectContent>
              {["", "token", "cookie"].map((s) => (
                <SelectItem key={s || "all"} value={s}>{s || "全部"}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1.5">
          <div className="text-xs text-stone-400">分组</div>
          <Select value={groupFilter} onValueChange={(v) => { setGroupFilter(v); setPage(1); }}>
            <SelectTrigger className="h-9 w-32"><SelectValue placeholder="全部" /></SelectTrigger>
            <SelectContent>
              <SelectItem value="">全部</SelectItem>
              {groups.map((g) => <SelectItem key={g} value={g}>{g}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1.5">
          <div className="text-xs text-stone-400">排序</div>
          <Select
            value={sortBy ? `${sortBy}:${sortOrder || "desc"}` : ""}
            onValueChange={(v) => {
              const [field, order] = v.split(":");
              setSortBy(field); setSortOrder(order); setPage(1);
            }}
          >
            <SelectTrigger className="h-9 w-40"><SelectValue placeholder="默认" /></SelectTrigger>
            <SelectContent>
              <SelectItem value="">默认</SelectItem>
              <SelectItem value="name:asc">名称 ↑</SelectItem>
              <SelectItem value="status:asc">状态 ↑</SelectItem>
              <SelectItem value="priority:desc">优先级 ↓</SelectItem>
              <SelectItem value="lastUsedAt:desc">最近使用 ↓</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <Button size="sm" variant="outline" onClick={() => { setPage(1); void load(1, q); }}>
          应用筛选
        </Button>
        <Button size="sm" variant="outline" onClick={() => setCreating(true)}>
          <Plus className="size-4" />新建账号
        </Button>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="outline" onClick={() => void runJob("全部探活", minimaxProbeAll)} disabled={busy !== ""}>
          {busy === "全部探活" ? <LoaderCircle className="size-4 animate-spin" /> : null}
          全部探活
        </Button>
        <Button size="sm" variant="outline" onClick={() => void runJob("刷新全部额度", minimaxQuotaAll)} disabled={busy !== ""}>
          {busy === "刷新全部额度" ? <LoaderCircle className="size-4 animate-spin" /> : null}
          刷新全部额度
        </Button>
        <Button size="sm" variant="outline" onClick={() => void runJob("清理无效号", () => minimaxCleanup({ statuses: ["invalid"] }))} disabled={busy !== ""}>
          {busy === "清理无效号" ? <LoaderCircle className="size-4 animate-spin" /> : null}
          清理无效号
        </Button>
        <Button size="sm" variant="outline" onClick={() => void exportAccounts()} disabled={busy !== ""}>
          {busy === "导出" ? <LoaderCircle className="size-4 animate-spin" /> : <Download className="size-4" />}
          导出
        </Button>
      </div>

      <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
        <CardContent className="space-y-2 p-5">
          <div className="text-sm font-semibold text-stone-900">导入账号</div>
          <Textarea
            value={importText}
            onChange={(event) => setImportText(event.target.value)}
            placeholder="每行一个账号，格式 email----password----clientId----refreshToken----auto/imap/graph"
            className="min-h-20 font-mono text-xs"
          />
          <Button size="sm" variant="outline" onClick={() => void importAccounts()} disabled={busy !== ""}>
            {busy === "导入" ? <LoaderCircle className="size-4 animate-spin" /> : <Upload className="size-4" />}
            导入
          </Button>
        </CardContent>
      </Card>

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
                            <Button size="sm" variant="ghost" onClick={() => void runOne(account.id, "credit")}>积分</Button>
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => {
                                setEditing(account);
                                setForm({
                                  name: String(account.name || ""),
                                  kind: String(account.kind || "token"),
                                  region: String(account.region || "global"),
                                  group: String(account.group || ""),
                                  remark: String(account.remark || ""),
                                  priority: String(account.priority ?? 50),
                                  maxConcurrent: String(account.maxConcurrent ?? 2),
                                });
                              }}
                            >
                              编辑
                            </Button>
                            <Button
                              size="sm"
                              variant="ghost"
                              className="text-rose-500"
                              onClick={() => void runJob("删除", () => minimaxAccountDelete(account.id))}
                            >
                              删除
                            </Button>
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

      <Dialog open={creating || Boolean(editing)} onOpenChange={(open) => { if (!open) { setCreating(false); setEditing(null); } }}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>{editing ? "编辑账号" : "新建账号"}</DialogTitle>
            <DialogDescription>token / cookie 类型账号的公共字段</DialogDescription>
          </DialogHeader>
          <div className="grid gap-3">
            <div className="space-y-1.5">
              <div className="text-xs text-stone-400">名称</div>
              <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <div className="text-xs text-stone-400">类型</div>
                <Select value={form.kind} onValueChange={(v) => setForm({ ...form, kind: v })}>
                  <SelectTrigger className="h-9"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="token">token</SelectItem>
                    <SelectItem value="cookie">cookie</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <div className="text-xs text-stone-400">区域</div>
                <Select value={form.region} onValueChange={(v) => setForm({ ...form, region: v })}>
                  <SelectTrigger className="h-9"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="global">global</SelectItem>
                    <SelectItem value="cn">cn</SelectItem>
                    <SelectItem value="auto">auto</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <div className="text-xs text-stone-400">分组</div>
                <Input value={form.group} onChange={(e) => setForm({ ...form, group: e.target.value })} />
              </div>
              <div className="space-y-1.5">
                <div className="text-xs text-stone-400">优先级</div>
                <Input value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value.replace(/\D/g, "") })} />
              </div>
            </div>
            <div className="space-y-1.5">
              <div className="text-xs text-stone-400">并发上限</div>
              <Input value={form.maxConcurrent} onChange={(e) => setForm({ ...form, maxConcurrent: e.target.value.replace(/\D/g, "") })} />
            </div>
            <div className="space-y-1.5">
              <div className="text-xs text-stone-400">备注</div>
              <Input value={form.remark} onChange={(e) => setForm({ ...form, remark: e.target.value })} />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => { setCreating(false); setEditing(null); }}>取消</Button>
            <Button onClick={() => void saveAccount()} disabled={busy !== ""}>
              {busy === "保存账号" ? <LoaderCircle className="size-4 animate-spin" /> : null}
              保存
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

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

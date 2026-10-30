"use client";

import { useEffect, useState } from "react";
import { Ban, CheckCircle2, ChevronLeft, ChevronRight, Download, LoaderCircle, RotateCcw, ScanSearch, Search, Trash2, Upload, UserPlus } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  fetchGrokAccounts,
  fetchGrokOverview,
  grokAccountBatch,
  grokAccountOps,
  grokAccountScript,
  grokBindEgress,
  grokDevicePoll,
  grokDeviceStart,
  grokExportAccounts,
  grokImportAccounts,
  type GrokAccount,
  type GrokSummary,
} from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";
import { httpRequest } from "@/lib/request";

const PAGE_SIZE = 20;
const PROVIDERS = [
  { key: "grok_build", label: "Grok Build" },
  { key: "grok_web", label: "Grok Web" },
  { key: "grok_console", label: "Grok Console" },
];

export default function GrokAccountsPage() {
  const { isCheckingAuth } = useAuthGuard(["admin"]);
  const [items, setItems] = useState<GrokAccount[]>([]);
  const [total, setTotal] = useState(0);
  const [summary, setSummary] = useState<GrokSummary | null>(null);
  const [page, setPage] = useState(1);
  const [query, setQuery] = useState("");
  const [provider, setProvider] = useState("grok_web");
  const [statusFilter, setStatusFilter] = useState("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [importProvider, setImportProvider] = useState("grok_web");
  const [importFile, setImportFile] = useState<File | null>(null);
  const [convertOpen, setConvertOpen] = useState(false);
  const [convertTarget, setConvertTarget] = useState<"build" | "console">("build");
  const [convertStrategy, setConvertStrategy] = useState<"missing" | "all">("missing");
  const [deviceOpen, setDeviceOpen] = useState(false);
  const [deviceEmail, setDeviceEmail] = useState("");
  const [devicePassword, setDevicePassword] = useState("");
  const [deviceInfo, setDeviceInfo] = useState<{ code?: string; uri?: string; sessionId?: string } | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      const [list, overview] = await Promise.all([
        fetchGrokAccounts({ page, pageSize: PAGE_SIZE, q: query.trim(), provider, authStatus: statusFilter === "all" ? "" : statusFilter }),
        fetchGrokOverview().catch(() => null),
      ]);
      setItems(list.items || []);
      setTotal(list.total ?? (list.items || []).length);
      if (overview?.summary) setSummary(overview.summary);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "读取失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!isCheckingAuth) void load();
  }, [isCheckingAuth, page, provider, statusFilter]);

  const runBatch = async (action: string, label: string) => {
    if (!selected.length) return;
    setBusy(true);
    try {
      await grokAccountBatch(action, selected, provider);
      toast.success(`${label} ${selected.length} 个账号`);
      setSelected([]);
      await load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "操作失败");
    } finally {
      setBusy(false);
    }
  };

  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const providerStat = (key: string) => summary?.providers?.[key] || { total: 0, available: 0 };

  return (
    <div className="space-y-4 pt-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-[11px] font-medium uppercase tracking-[0.28em] text-stone-400">Grok</p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-stone-900">上游账号</h1>
        </div>
        <Button className="h-9 rounded-xl" onClick={() => setImportOpen(true)}>
          <Upload className="size-4" /> 账号接入
        </Button>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {PROVIDERS.map((item) => (
          <Card key={item.key} className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
            <CardContent className="space-y-1 p-4">
              <p className="text-xs text-stone-400">{item.label} 账号数</p>
              <p className="text-2xl font-semibold text-stone-900">{providerStat(item.key).total ?? 0}</p>
              <p className="text-[11px] text-stone-400">可调度 {providerStat(item.key).available ?? 0}</p>
            </CardContent>
          </Card>
        ))}
        <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
          <CardContent className="space-y-1 p-4">
            <p className="text-xs text-stone-400">异常账号数</p>
            <p className="text-2xl font-semibold text-rose-500">{summary?.attention ?? 0}</p>
            <p className="text-[11px] text-stone-400">
              恢复中 {summary?.recovering ?? 0} · 失败 {(summary?.issues?.reauthRequired ?? 0) + (summary?.issues?.disabled ?? 0)}
            </p>
          </CardContent>
        </Card>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <div className="flex rounded-xl bg-stone-100 p-1">
          {PROVIDERS.map((item) => (
            <button
              key={item.key}
              type="button"
              onClick={() => { setProvider(item.key); setPage(1); setSelected([]); }}
              className={`rounded-lg px-3 py-1.5 text-xs font-medium transition ${provider === item.key ? "bg-white text-stone-900 shadow-sm" : "text-stone-500"}`}
            >
              {item.label}
            </button>
          ))}
        </div>
        <div className="relative ml-auto">
          <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-stone-400" />
          <Input
            className="h-9 w-64 rounded-xl pl-9"
            placeholder="搜索账号"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => { if (event.key === "Enter") { setPage(1); void load(); } }}
          />
        </div>
        <Select value={statusFilter} onValueChange={(value) => { setStatusFilter(value); setPage(1); setSelected([]); }}>
          <SelectTrigger className="h-9 w-32 rounded-xl"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部状态</SelectItem>
            <SelectItem value="active">正常</SelectItem>
            <SelectItem value="reauthRequired">需重认证</SelectItem>
            <SelectItem value="disabled">已停用</SelectItem>
          </SelectContent>
        </Select>
        <Button className="h-9 rounded-xl" onClick={() => { setPage(1); void load(); }}>查询</Button>
        <Button
          variant="outline" className="h-9 rounded-xl" disabled={busy}
          onClick={async () => {
            setBusy(true);
            try {
              await grokAccountOps({ action: "detect", provider });
              toast.success("已提交检测");
              await load();
            } catch (err) { toast.error(err instanceof Error ? err.message : "检测失败"); } finally { setBusy(false); }
          }}
        >
          <ScanSearch className="size-4" /> 检测账号
        </Button>
        <Button
          variant="outline" className="h-9 rounded-xl" disabled={busy}
          onClick={async () => {
            setBusy(true);
            try {
              const preview = await grokAccountOps({ action: "cleanup_preview", provider, statuses: ["reauthRequired", "disabled"] });
              const count = Number(((preview as { data?: { total?: number } })?.data?.total) ?? 0);
              if (!window.confirm(`将清理 ${count} 个异常账号，确认？`)) return;
              await grokAccountOps({ action: "cleanup", provider, statuses: ["reauthRequired", "disabled"] });
              toast.success("已执行清理");
              await load();
            } catch (err) { toast.error(err instanceof Error ? err.message : "清理失败"); } finally { setBusy(false); }
          }}
        >
          <Trash2 className="size-4" /> 账号清理
        </Button>
        <Button
          variant="outline" className="h-9 rounded-xl" disabled={busy}
          onClick={async () => {
            setBusy(true);
            try {
              const data = await grokExportAccounts(provider);
              const blob = new Blob([data.content || ""], { type: "application/json" });
              const url = URL.createObjectURL(blob);
              const link = document.createElement("a");
              link.href = url;
              link.download = `grok-${provider}-${Date.now()}.json`;
              link.click();
              URL.revokeObjectURL(url);
              toast.success("已导出");
            } catch (err) { toast.error(err instanceof Error ? err.message : "导出失败"); } finally { setBusy(false); }
          }}
        >
          <Download className="size-4" /> 导出
        </Button>
        <Button variant="outline" className="h-9 rounded-xl" onClick={() => setDeviceOpen(true)}>
          <UserPlus className="size-4" /> 设备码登录
        </Button>
      </div>

      {selected.length ? (
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-indigo-100 bg-indigo-50/60 px-3 py-2 text-xs">
          <span className="font-medium text-indigo-700">已选择 {selected.length} 项</span>
          <Button size="sm" variant="ghost" className="h-7 rounded-lg" disabled={busy} onClick={() => void runBatch("enable", "已启用")}>
            <CheckCircle2 className="size-3.5" /> 启用
          </Button>
          <Button size="sm" variant="ghost" className="h-7 rounded-lg" disabled={busy} onClick={() => void runBatch("disable", "已停用")}>
            <Ban className="size-3.5" /> 停用
          </Button>
          <Button
            size="sm" variant="ghost" className="h-7 rounded-lg" disabled={busy}
            onClick={async () => {
              const raw = window.prompt("设置并发数", "8");
              if (!raw) return;
              setBusy(true);
              try {
                const data = await httpRequest<{ results?: Array<{ id: string; ok: boolean; maxConcurrent?: number; error?: string }> }>(
                  "/api/grok/accounts/ops",
                  { method: "POST", body: { action: "set_concurrency", ids: selected, provider, value: Number(raw) || 8 } },
                );
                const list = data.results || [];
                const okCount = list.filter((item) => item.ok).length;
                const applied = list.find((item) => item.ok)?.maxConcurrent;
                if (okCount) toast.success(`并发已设为 ${applied ?? raw}：成功 ${okCount} / ${list.length}`);
                else toast.error(`设置失败：${list[0]?.error || "上游未返回结果"}`);
                await load();
              } catch (err) { toast.error(err instanceof Error ? err.message : "设置失败"); } finally { setBusy(false); }
            }}
          >
            设置并发
          </Button>
          <Button
            size="sm" variant="ghost" className="h-7 rounded-lg" disabled={busy}
            onClick={() => setConvertOpen(true)}
          >
            账号转换
          </Button>
          <Button
            size="sm" variant="ghost" className="hidden"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                const data = await grokAccountOps({ action: "convert_to_build", ids: selected, provider });
                const stream = String((data as { stream?: string }).stream || "");
                const lines = stream.trim().split(String.fromCharCode(10)).filter((line) => line.startsWith("data: "));
                let summary: Record<string, number> = {};
                for (const line of lines.reverse()) {
                  try {
                    const parsed = JSON.parse(line.slice(6));
                    if (parsed && typeof parsed === "object" && ("created" in parsed || "linked" in parsed || "skipped" in parsed || "failed" in parsed)) {
                      summary = parsed as Record<string, number>;
                      break;
                    }
                  } catch {
                    /* 跳过非 JSON 行 */
                  }
                }
                const created = Number(summary.created ?? 0);
                const linked = Number(summary.linked ?? 0);
                const skipped = Number(summary.skipped ?? 0);
                const failed = Number(summary.failed ?? 0);
                if (created + linked > 0) {
                  toast.success(`转换完成：新增 ${created} · 关联 ${linked} · 跳过 ${skipped} · 失败 ${failed}`);
                } else if (failed > 0) {
                  toast.error(`转换失败 ${failed} 个：${stream.slice(-160)}`);
                } else {
                  toast.warning(`上游没有转换任何账号（跳过 ${skipped}）：这些 Web 号可能缺少 Console 关联或已转换过`);
                }
                await load();
              } catch (err) { toast.error(err instanceof Error ? err.message : "转换失败"); } finally { setBusy(false); }
            }}
          >
            账号转换
          </Button>
          <Button
            size="sm" variant="ghost" className="h-7 rounded-lg" disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                const data = await grokAccountOps({ action: "reset_quota", ids: selected, provider });
                const results = ((data as { results?: Array<{ ok: boolean; error?: string }> }).results) || [];
                const okCount = results.filter((item) => item.ok).length;
                if (okCount === results.length && okCount > 0) toast.success(`额度已同步 ${okCount} 个账号`);
                else toast.error(`额度同步：成功 ${okCount} / ${results.length}｜${results.find((item) => !item.ok)?.error || ""}`);
                await load();
              } catch (err) { toast.error(err instanceof Error ? err.message : "同步失败"); } finally { setBusy(false); }
            }}
          >
            <RotateCcw className="size-3.5" /> 额度同步
          </Button>
          <Button
            size="sm" variant="ghost" className="h-7 rounded-lg" disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                const data = await grokAccountOps({ action: "refresh_tokens", ids: selected, provider });
                const stream = String((data as { stream?: string }).stream || "");
                const done = /"succeeded"\s*:\s*(\d+)/.exec(stream);
                const failed = /"failed"\s*:\s*(\d+)/.exec(stream);
                if (done || failed) {
                  toast.success(`凭据刷新：成功 ${done?.[1] ?? 0} · 失败 ${failed?.[1] ?? 0}`);
                } else if (stream.includes("仅 Grok Build")) {
                  toast.warning("只有 Grok Build 账号支持凭据刷新");
                } else {
                  toast.warning(`上游返回：${stream.slice(-160) || "无内容"}`);
                }
                await load();
              } catch (err) { toast.error(err instanceof Error ? err.message : "刷新失败"); } finally { setBusy(false); }
            }}
          >
            凭据刷新
          </Button>
          <Button
            size="sm" variant="ghost" className="h-7 rounded-lg" disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                for (const id of selected) {
                  await grokAccountScript(id, "accept_terms");
                  await grokAccountScript(id, "birth_date");
                  await grokAccountScript(id, "nsfw");
                }
                toast.success("已执行账号工具脚本");
              } catch (err) { toast.error(err instanceof Error ? err.message : "脚本失败"); } finally { setBusy(false); }
            }}
          >
            账号工具
          </Button>
          <Button size="sm" variant="ghost" className="h-7 rounded-lg text-rose-600" disabled={busy} onClick={() => void runBatch("delete", "已删除")}>
            <Trash2 className="size-3.5" /> 删除
          </Button>
        </div>
      ) : null}

      <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
        <CardContent className="p-0">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[960px] text-left text-sm">
              <thead className="border-b border-stone-100 text-[11px] uppercase tracking-[0.18em] text-stone-400">
                <tr>
                  <th className="w-10 px-4 py-3">
                    <input
                      type="checkbox"
                      className="size-4 rounded border-stone-300"
                      checked={items.length > 0 && items.every((item) => selected.includes(item.id))}
                      onChange={(event) =>
                        setSelected(event.target.checked ? items.map((item) => item.id) : [])
                      }
                    />
                  </th>
                  <th className="px-4 py-3">账号</th>
                  <th className="px-4 py-3">类型</th>
                  <th className="px-4 py-3">状态</th>
                  <th className="px-4 py-3">优先级</th>
                  <th className="px-4 py-3">并发</th>
                  <th className="px-4 py-3">额度</th>
                  <th className="px-4 py-3">凭据续期</th>
                  <th className="px-4 py-3">创建时间</th>
                  <th className="px-4 py-3">失败</th>
                  <th className="px-4 py-3">操作</th>
                </tr>
              </thead>
              <tbody>
                {loading ? (
                  <tr><td colSpan={11} className="px-4 py-10 text-center"><LoaderCircle className="mx-auto size-4 animate-spin text-stone-400" /></td></tr>
                ) : items.length === 0 ? (
                  <tr><td colSpan={11} className="px-4 py-10 text-center text-stone-400">{error || "没有账号"}</td></tr>
                ) : (
                  items.map((account) => (
                    <tr key={account.id} className="border-b border-stone-100/80 text-stone-600 hover:bg-stone-50/70">
                      <td className="px-4 py-3">
                        <input
                          type="checkbox"
                          className="size-4 rounded border-stone-300"
                          checked={selected.includes(account.id)}
                          onChange={(event) =>
                            setSelected((prev) => event.target.checked ? [...prev, account.id] : prev.filter((item) => item !== account.id))
                          }
                        />
                      </td>
                      <td className="px-4 py-3">
                        <div className="font-medium text-stone-700">{account.email || account.name}</div>
                        <div className="text-xs text-stone-400">{account.name}</div>
                      </td>
                      <td className="px-4 py-3">
                        <Badge variant="outline" className="rounded-md border-stone-200 text-stone-600">
                          {account.provider === "grok_build" ? "Build" : account.provider === "grok_console" ? "Console" : "Web"}
                        </Badge>
                      </td>
                      <td className="px-4 py-3">
                        <Badge
                          variant="outline"
                          className={`rounded-md ${account.enabled && account.authStatus === "active" ? "border-emerald-200 text-emerald-600" : "border-rose-200 text-rose-500"}`}
                        >
                          {account.enabled ? (account.authStatus === "active" ? "正常" : account.authStatus || "正常") : "停用"}
                        </Badge>
                      </td>
                      <td className="px-4 py-3 text-xs">{account.priority ?? "—"}</td>
                      <td className="px-4 py-3 text-xs">{account.maxConcurrent ?? "—"}</td>
                      <td
                        className="px-4 py-3 text-xs text-stone-500"
                      >
                        {account.quota?.limitKnown
                          ? `${account.quota?.remaining ?? 0} / ${account.quota?.limit ?? 0}`
                          : account.quotaWindows?.length
                            ? `${account.quotaWindows[0].remaining ?? 0} / ${account.quotaWindows[0].total ?? 0}`
                            : "—"}
                      </td>
                      <td className="px-4 py-3 text-xs">
                        <span className={account.refreshable ? "text-emerald-600" : "text-stone-400"}>
                          {account.refreshable ? "支持自动续期" : "—"}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-xs text-stone-500">{account.createdAt ? account.createdAt.slice(0, 10) : "—"}</td>
                      <td className="px-4 py-3 text-xs">{account.failureCount ?? 0}</td>
                      <td className="px-4 py-3">
                        <details className="relative">
                          <summary className="cursor-pointer list-none rounded-lg px-2 py-1 text-stone-400 hover:bg-stone-100">…</summary>
                          <div className="absolute right-0 z-20 mt-1 w-36 rounded-xl border border-stone-200 bg-white py-1 text-xs shadow-lg">
                            {[
                              { label: "启用", run: () => grokAccountBatch("enable", [account.id], account.provider) },
                              { label: "停用", run: () => grokAccountBatch("disable", [account.id], account.provider) },
                              { label: "重置额度", run: () => grokAccountBatch("reset_quota", [account.id], account.provider) },
                              { label: "刷新 Token", run: () => grokAccountOps({ action: "refresh_tokens", ids: [account.id], provider: account.provider }) },
                              { label: "删除", run: () => grokAccountBatch("delete", [account.id], account.provider) },
                            ].map((item) => (
                              <button
                                key={item.label}
                                type="button"
                                className="block w-full px-3 py-1.5 text-left text-stone-600 hover:bg-stone-50"
                                onClick={async () => {
                                  try {
                                    await item.run();
                                    toast.success(item.label + " 已执行");
                                    await load();
                                  } catch (err) {
                                    toast.error(err instanceof Error ? err.message : "操作失败");
                                  }
                                }}
                              >
                                {item.label}
                              </button>
                            ))}
                            <button
                              type="button"
                              className="block w-full px-3 py-1.5 text-left text-stone-600 hover:bg-stone-50"
                              onClick={async () => {
                                const nodeId = window.prompt("绑定的出口节点 ID（留空 = 自动分配）", "");
                                if (nodeId === null) return;
                                try {
                                  await grokBindEgress(account.id, nodeId.trim(), nodeId.trim() ? "manual" : "auto");
                                  toast.success("代理配置已更新");
                                  await load();
                                } catch (err) {
                                  toast.error(err instanceof Error ? err.message : "配置失败");
                                }
                              }}
                            >
                              代理配置
                            </button>
                          </div>
                        </details>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
          <div className="flex items-center justify-between border-t border-stone-100 px-4 py-3 text-xs text-stone-500">
            <span>第 {page} / {pages} 页 · 共 {total} 个</span>
            <div className="flex items-center gap-2">
              <Button variant="ghost" className="h-8 rounded-lg px-2" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}><ChevronLeft className="size-4" /></Button>
              <Button variant="ghost" className="h-8 rounded-lg px-2" disabled={page >= pages} onClick={() => setPage((p) => p + 1)}><ChevronRight className="size-4" /></Button>
            </div>
          </div>
        </CardContent>
      </Card>

      <Dialog open={convertOpen} onOpenChange={setConvertOpen}>
        <DialogContent className="rounded-2xl p-6">
          <DialogHeader>
            <DialogTitle>账号转换</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <p className="text-xs text-stone-500">选择目标渠道和转换范围，处理所选 {selected.length} 个 Grok Web 账号。</p>
            <div>
              <p className="mb-2 text-xs font-medium text-stone-600">目标渠道</p>
              <div className="grid grid-cols-2 gap-2">
                {[
                  { key: "build" as const, label: "Grok Build" },
                  { key: "console" as const, label: "Grok Console" },
                ].map((item) => (
                  <button
                    key={item.key}
                    type="button"
                    onClick={() => setConvertTarget(item.key)}
                    className={`rounded-xl border px-3 py-2 text-sm transition ${convertTarget === item.key ? "border-indigo-300 bg-indigo-50 text-indigo-700" : "border-stone-200 text-stone-600"}`}
                  >
                    {item.label}
                  </button>
                ))}
              </div>
            </div>
            <div>
              <p className="mb-2 text-xs font-medium text-stone-600">转换范围</p>
              <div className="grid grid-cols-2 gap-2">
                {[
                  { key: "missing" as const, label: "仅转换缺失" },
                  { key: "all" as const, label: "重新转换全部" },
                ].map((item) => (
                  <button
                    key={item.key}
                    type="button"
                    onClick={() => setConvertStrategy(item.key)}
                    className={`rounded-xl border px-3 py-2 text-sm transition ${convertStrategy === item.key ? "border-indigo-300 bg-indigo-50 text-indigo-700" : "border-stone-200 text-stone-600"}`}
                  >
                    {item.label}
                  </button>
                ))}
              </div>
              <p className="mt-2 text-[11px] text-stone-400">
                {convertStrategy === "missing"
                  ? "仅为未关联目标渠道的账号执行 Device OAuth 转换；已有转换保持不变。"
                  : "为全部目标重新执行 Device OAuth 转换，并创建或更新对应账号。"}
              </p>
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setConvertOpen(false)}>取消</Button>
            <Button
              disabled={busy || selected.length === 0}
              onClick={async () => {
                setBusy(true);
                try {
                  const data = await grokAccountOps({ action: "convert", ids: selected, provider, target: convertTarget, strategy: convertStrategy } as never);
                  const stream = String((data as { stream?: string }).stream || "");
                  const lines = stream.trim().split(String.fromCharCode(10)).filter((line) => line.startsWith("data: "));
                  let summary: Record<string, number> = {};
                  for (const line of lines.reverse()) {
                    try {
                      const parsed = JSON.parse(line.slice(6));
                      if (parsed && typeof parsed === "object" && ("created" in parsed || "linked" in parsed || "skipped" in parsed || "failed" in parsed)) {
                        summary = parsed as Record<string, number>;
                        break;
                      }
                    } catch { /* ignore */ }
                  }
                  const created = Number(summary.created ?? 0);
                  const linked = Number(summary.linked ?? 0);
                  const skipped = Number(summary.skipped ?? 0);
                  const failed = Number(summary.failed ?? 0);
                  if (created + linked > 0) toast.success(`转换完成：新增 ${created} · 关联 ${linked} · 跳过 ${skipped} · 失败 ${failed}`);
                  else if (failed > 0) toast.error(`转换失败 ${failed} 个：${stream.slice(-160)}`);
                  else toast.warning(`上游没有转换任何账号（跳过 ${skipped}）`);
                  setConvertOpen(false);
                  await load();
                } catch (err) {
                  toast.error(err instanceof Error ? err.message : "转换失败");
                } finally {
                  setBusy(false);
                }
              }}
            >
              开始转换
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={importOpen} onOpenChange={setImportOpen}>
        <DialogContent className="rounded-2xl p-6">
          <DialogHeader><DialogTitle>账号接入</DialogTitle></DialogHeader>
          <div className="space-y-3">
            <Select value={importProvider} onValueChange={setImportProvider}>
              <SelectTrigger className="h-10 rounded-xl"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="grok_web">Grok Web</SelectItem>
                <SelectItem value="grok_build">Grok Build</SelectItem>
                <SelectItem value="grok_console">Grok Console</SelectItem>
              </SelectContent>
            </Select>
            <input
              type="file"
              accept=".txt,.json,.csv"
              className="w-full rounded-xl border border-stone-200 px-3 py-2 text-sm"
              onChange={(event) => setImportFile(event.target.files?.[0] || null)}
            />
            <p className="text-xs text-stone-400">
              {importFile ? `已选择：${importFile.name}` : "支持 grok2api 导出的 txt / json / csv"}
            </p>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setImportOpen(false)}>取消</Button>
            <Button
              disabled={!importFile || busy}
              onClick={async () => {
                if (!importFile) return;
                setBusy(true);
                try {
                  const data = await grokImportAccounts(importFile, importProvider);
                  const stream = String(data.stream || "");
                  const tail = stream.trim().split(String.fromCharCode(10)).slice(-2).join(" ");
                  toast.success(`已提交导入：${tail.slice(0, 120) || "完成"}`);
                  setImportOpen(false);
                  setImportFile(null);
                  await load();
                } catch (err) {
                  toast.error(err instanceof Error ? err.message : "导入失败");
                } finally {
                  setBusy(false);
                }
              }}
            >
              <Upload className="size-4" /> 开始导入
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={deviceOpen} onOpenChange={setDeviceOpen}>
        <DialogContent className="rounded-2xl p-6">
          <DialogHeader><DialogTitle>设备码登录</DialogTitle></DialogHeader>
          <div className="space-y-3">
            <Input className="h-10 rounded-xl" placeholder="邮箱" value={deviceEmail} onChange={(e) => setDeviceEmail(e.target.value)} />
            <Input className="h-10 rounded-xl" type="password" placeholder="密码" value={devicePassword} onChange={(e) => setDevicePassword(e.target.value)} />
            {deviceInfo?.code ? (
              <div className="rounded-xl bg-stone-100 p-3 text-sm">
                <p>验证码：<span className="font-mono text-base font-semibold">{deviceInfo.code}</span></p>
                <p className="text-xs text-stone-500">打开 {deviceInfo.uri} 完成授权</p>
              </div>
            ) : null}
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setDeviceOpen(false)}>关闭</Button>
            <Button
              onClick={async () => {
                try {
                  const data = await grokDeviceStart(deviceEmail.trim(), devicePassword);
                  setDeviceInfo({ code: data.userCode, uri: data.verificationUri, sessionId: data.sessionId });
                  toast.success("已生成设备码");
                  const timer = setInterval(async () => {
                    try {
                      const result = await grokDevicePoll(data.sessionId);
                      if (result.status && result.status !== "pending") {
                        clearInterval(timer);
                        toast.success(`授权状态：${result.status}`);
                        await load();
                      }
                    } catch {
                      clearInterval(timer);
                    }
                  }, (data.intervalSeconds || 5) * 1000);
                } catch (err) {
                  toast.error(err instanceof Error ? err.message : "发起失败");
                }
              }}
            >
              生成验证码
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

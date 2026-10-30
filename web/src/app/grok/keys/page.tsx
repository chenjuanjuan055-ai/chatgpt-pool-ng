"use client";

import { useEffect, useState } from "react";
import { Copy, Eye, KeyRound, LoaderCircle, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { createGrokKey, deleteGrokKey, fetchGrokKeys, fetchGrokModels, patchGrokKey, revealGrokKey, type GrokKey } from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";

export default function GrokKeysPage() {
  const { isCheckingAuth } = useAuthGuard(["admin"]);
  const [items, setItems] = useState<GrokKey[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [rpm, setRpm] = useState("120");
  const [concurrency, setConcurrency] = useState("8");
  const [budget, setBudget] = useState("0");
  const [models, setModels] = useState<string[]>([]);
  const [allowed, setAllowed] = useState<string[]>([]);
  const [secret, setSecret] = useState("");

  const load = async () => {
    setLoading(true);
    try {
      const data = await fetchGrokKeys();
      setItems(data.items || []);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "读取失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (isCheckingAuth) return;
    void load();
    void (async () => {
      try {
        const data = await fetchGrokModels();
        setModels((data.items || []).map((item) => item.id));
      } catch {
        /* 忽略 */
      }
    })();
  }, [isCheckingAuth]);

  const handleCreate = async () => {
    if (!name.trim()) {
      toast.error("请输入名称");
      return;
    }
    try {
      const data = await createGrokKey(name.trim(), { rpmLimit: Number(rpm) || 120, maxConcurrent: Number(concurrency) || 8, billingLimitUsd: Number(budget) || 0, allowedModels: allowed.length ? allowed : undefined });
      setSecret((data as { secret?: string }).secret || "");
      setName("");
      setOpen(false);
      toast.success("已创建，密钥只显示这一次");
      await load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "创建失败");
    }
  };

  return (
    <div className="space-y-4 pt-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-[11px] font-medium uppercase tracking-[0.28em] text-stone-400">Grok</p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-stone-900">客户端密钥</h1>
          <p className="mt-1 text-sm text-stone-500">调用 Grok 接口用的 key，可随时吊销。</p>
        </div>
        <Button className="h-9 rounded-xl" onClick={() => setOpen(true)}>
          <Plus className="size-4" /> 新建密钥
        </Button>
      </div>

      {secret ? (
        <Card className="rounded-2xl border-emerald-200 bg-emerald-50/60">
          <CardContent className="flex flex-wrap items-center justify-between gap-3 p-4 text-sm text-emerald-700">
            <span className="break-all font-mono text-xs">{secret}</span>
            <Button
              variant="ghost"
              className="h-8 rounded-lg px-2 text-emerald-700"
              onClick={() => void navigator.clipboard.writeText(secret)}
            >
              <Copy className="size-4" /> 复制
            </Button>
          </CardContent>
        </Card>
      ) : null}

      <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
        <CardContent className="p-0">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[820px] text-left text-sm">
              <thead className="border-b border-stone-100 text-[11px] uppercase tracking-[0.18em] text-stone-400">
                <tr>
                  <th className="px-4 py-3">名称</th>
                  <th className="px-4 py-3">前缀</th>
                  <th className="px-4 py-3">状态</th>
                  <th className="px-4 py-3">RPM / 并发</th>
                  <th className="px-4 py-3">最近使用</th>
                  <th className="px-4 py-3">操作</th>
                </tr>
              </thead>
              <tbody>
                {loading ? (
                  <tr>
                    <td colSpan={6} className="px-4 py-10 text-center">
                      <LoaderCircle className="mx-auto size-4 animate-spin text-stone-400" />
                    </td>
                  </tr>
                ) : items.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="px-4 py-10 text-center text-stone-400">{error || "还没有密钥"}</td>
                  </tr>
                ) : (
                  items.map((key) => (
                    <tr key={key.id} className="border-b border-stone-100/80 hover:bg-stone-50/70">
                      <td className="px-4 py-3 font-medium text-stone-700">{key.name}</td>
                      <td className="px-4 py-3 font-mono text-xs text-stone-500">{key.prefix}</td>
                      <td className="px-4 py-3">
                        <Badge variant="outline" className={`rounded-md ${key.enabled ? "border-emerald-200 text-emerald-600" : "border-stone-200 text-stone-500"}`}>
                          {key.enabled ? "启用" : "停用"}
                        </Badge>
                      </td>
                      <td className="px-4 py-3 text-xs">{key.rpmLimit} / {key.maxConcurrent}</td>
                      <td className="px-4 py-3 text-xs text-stone-500">{key.lastUsedAt ? key.lastUsedAt.slice(0, 19).replace("T", " ") : "—"}</td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-1">
                          <Button
                            variant="ghost"
                            className="h-8 rounded-lg px-2 text-stone-500"
                            onClick={async () => {
                              try {
                                await patchGrokKey(key.id, { enabled: !key.enabled });
                                toast.success(key.enabled ? "已停用" : "已启用");
                                await load();
                              } catch (err) {
                                toast.error(err instanceof Error ? err.message : "切换失败");
                              }
                            }}
                          >
                            {key.enabled ? "停用" : "启用"}
                          </Button>
                          <Button
                            variant="ghost"
                            className="h-8 rounded-lg px-2 text-stone-500"
                            onClick={async () => {
                              try {
                                const data = await revealGrokKey(key.id);
                                setSecret(data.secret);
                                toast.success("已取回密钥");
                              } catch (err) {
                                toast.error(err instanceof Error ? err.message : "取回失败");
                              }
                            }}
                          >
                            <Eye className="size-4" />
                          </Button>
                          <Button
                            variant="ghost"
                            className="h-8 rounded-lg px-2 text-rose-500"
                            onClick={async () => {
                              if (!window.confirm(`删除密钥 ${key.name}？`)) return;
                              try {
                                await deleteGrokKey(key.id);
                                toast.success("已删除");
                                await load();
                              } catch (err) {
                                toast.error(err instanceof Error ? err.message : "删除失败");
                              }
                            }}
                          >
                            <Trash2 className="size-4" />
                          </Button>
                        </div>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="rounded-2xl p-6">
          <DialogHeader>
            <DialogTitle>新建客户端密钥</DialogTitle>
          </DialogHeader>
          <div className="space-y-2">
            <Input className="h-10 rounded-xl" placeholder="名称，例如 我的应用" value={name} onChange={(event) => setName(event.target.value)} />
            <div className="grid grid-cols-3 gap-2">
              <div>
                <p className="mb-1 text-xs text-stone-500">RPM</p>
                <Input className="h-9 rounded-xl" value={rpm} onChange={(e) => setRpm(e.target.value)} />
              </div>
              <div>
                <p className="mb-1 text-xs text-stone-500">并发</p>
                <Input className="h-9 rounded-xl" value={concurrency} onChange={(e) => setConcurrency(e.target.value)} />
              </div>
              <div>
                <p className="mb-1 text-xs text-stone-500">额度上限(USD)</p>
                <Input className="h-9 rounded-xl" value={budget} onChange={(e) => setBudget(e.target.value)} />
              </div>
            </div>
            <div>
              <p className="mb-1 text-xs text-stone-500">可用模型（不选=全部）</p>
              <div className="flex max-h-24 flex-wrap gap-1 overflow-y-auto rounded-xl border border-stone-200 p-2">
                {models.map((model) => (
                  <label key={model} className="flex items-center gap-1 text-[11px] text-stone-600">
                    <input
                      type="checkbox"
                      className="size-3"
                      checked={allowed.includes(model)}
                      onChange={(event) =>
                        setAllowed((prev) => event.target.checked ? [...prev, model] : prev.filter((item) => item !== model))
                      }
                    />
                    {model}
                  </label>
                ))}
              </div>
            </div>
            <p className="text-xs text-stone-400">创建后密钥只显示一次，请及时复制。</p>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setOpen(false)}>取消</Button>
            <Button onClick={() => void handleCreate()}>
              <KeyRound className="size-4" /> 创建
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

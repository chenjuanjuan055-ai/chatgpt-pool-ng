"use client";

import { useEffect, useState } from "react";
import { Activity, LoaderCircle, Pencil, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { fetchGrokEgress, grokEgressCreate, grokEgressDelete, grokEgressTest, grokEgressUpdate, type GrokEgressNode } from "@/lib/api";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { useAuthGuard } from "@/lib/use-auth-guard";

export default function GrokEgressPage() {
  const { isCheckingAuth } = useAuthGuard(["admin"]);
  const [items, setItems] = useState<GrokEgressNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [kind, setKind] = useState("socks5");
  const [editing, setEditing] = useState<GrokEgressNode | null>(null);

  useEffect(() => {
    if (isCheckingAuth) return;
    void (async () => {
      try {
        const data = await fetchGrokEgress(1, 50);
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
        <h1 className="mt-1 text-2xl font-semibold tracking-tight text-stone-900">出口节点</h1>
        <p className="mt-1 text-sm text-stone-500">Grok 账号绑定的出口代理。</p>
      </div>
      <Button
        variant="outline"
        className="h-9 rounded-xl"
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          try {
            await grokEgressTest([]);
            toast.success("已提交全部节点测速");
          } catch (err) {
            toast.error(err instanceof Error ? err.message : "测速失败");
          } finally {
            setBusy(false);
          }
        }}
      >
        <Activity className="size-4" /> 全部测速
      </Button>
      <Button className="h-9 rounded-xl" onClick={() => setOpen(true)}>
        <Plus className="size-4" /> 新建节点
      </Button>

      <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
        <CardContent className="p-0">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[820px] text-left text-sm">
              <thead className="border-b border-stone-100 text-[11px] uppercase tracking-[0.18em] text-stone-400">
                <tr>
                  <th className="px-4 py-3">名称</th>
                  <th className="px-4 py-3">代理</th>
                  <th className="px-4 py-3">出口 IP</th>
                  <th className="px-4 py-3">健康</th>
                  <th className="px-4 py-3">绑定账号</th>
                  <th className="px-4 py-3">延迟</th>
                  <th className="px-4 py-3">操作</th>
                </tr>
              </thead>
              <tbody>
                {loading ? (
                  <tr>
                    <td colSpan={7} className="px-4 py-10 text-center">
                      <LoaderCircle className="mx-auto size-4 animate-spin text-stone-400" />
                    </td>
                  </tr>
                ) : items.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="px-4 py-10 text-center text-stone-400">{error || "暂无出口节点"}</td>
                  </tr>
                ) : (
                  items.map((node) => (
                    <tr key={node.id} className="border-b border-stone-100/80 text-stone-600 hover:bg-stone-50/70">
                      <td className="px-4 py-3 font-medium text-stone-700">{node.name || node.id}</td>
                      <td className="px-4 py-3 text-xs text-stone-500">{node.proxyDisplay || "—"}</td>
                      <td className="px-4 py-3 font-mono text-xs text-stone-500">{node.exitIp || "—"}</td>
                      <td className="px-4 py-3">
                        <Badge
                          variant="outline"
                          className={`rounded-md ${node.health === "healthy" || node.enabled ? "border-emerald-200 text-emerald-600" : "border-rose-200 text-rose-500"}`}
                        >
                          {typeof node.health === "number"
                            ? `${Math.round(node.health * 100)}%`
                            : node.health || node.probeStatus || (node.enabled ? "启用" : "停用")}
                        </Badge>
                      </td>
                      <td className="px-4 py-3 text-xs">{node.assignedAccountCount ?? 0} / {node.accountCapacity ?? "—"}</td>
                      <td className="px-4 py-3 text-xs">{node.probeLatencyMs != null ? `${node.probeLatencyMs} ms` : "—"}</td>
                      <td className="px-4 py-3">
                        <Button
                          variant="ghost"
                          className="h-8 rounded-lg px-2 text-stone-500"
                          onClick={() => setEditing(node)}
                        >
                          <Pencil className="size-4" />
                        </Button>
                        <Button
                          variant="ghost"
                          className="h-8 rounded-lg px-2 text-rose-500"
                          onClick={async () => {
                            if (!window.confirm(`删除节点 ${node.name || node.id}？`)) return;
                            try {
                              await grokEgressDelete(node.id);
                              toast.success("已删除");
                              const data = await fetchGrokEgress(1, 50);
                              setItems(data.items || []);
                            } catch (err) {
                              toast.error(err instanceof Error ? err.message : "删除失败");
                            }
                          }}
                        >
                          <Trash2 className="size-4" />
                        </Button>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
      <Dialog open={Boolean(editing)} onOpenChange={(value) => (!value ? setEditing(null) : null)}>
        <DialogContent className="rounded-2xl p-6">
          <DialogHeader><DialogTitle>编辑出口节点</DialogTitle></DialogHeader>
          <div className="space-y-3">
            <Input className="h-10 rounded-xl" placeholder="名称" defaultValue={editing?.name || ""} onChange={(e) => setName(e.target.value)} />
            <Input className="h-10 rounded-xl" placeholder="地址" defaultValue={editing?.host || ""} onChange={(e) => setUrl(e.target.value)} />
            <Input className="h-10 rounded-xl" placeholder="类型" defaultValue={editing?.kind || ""} onChange={(e) => setKind(e.target.value)} />
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setEditing(null)}>取消</Button>
            <Button
              onClick={async () => {
                if (!editing) return;
                try {
                  await grokEgressUpdate(editing.id, { name: name || editing.name });
                  toast.success("已保存");
                  setEditing(null);
                  const data = await fetchGrokEgress(1, 50);
                  setItems(data.items || []);
                } catch (err) {
                  toast.error(err instanceof Error ? err.message : "保存失败");
                }
              }}
            >
              保存
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="rounded-2xl p-6">
          <DialogHeader><DialogTitle>新建出口节点</DialogTitle></DialogHeader>
          <div className="space-y-3">
            <Input className="h-10 rounded-xl" placeholder="名称" value={name} onChange={(e) => setName(e.target.value)} />
            <Input className="h-10 rounded-xl" placeholder="地址，如 socks5://1.2.3.4:1080" value={url} onChange={(e) => setUrl(e.target.value)} />
            <Input className="h-10 rounded-xl" placeholder="类型，如 socks5 / http" value={kind} onChange={(e) => setKind(e.target.value)} />
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setOpen(false)}>取消</Button>
            <Button
              onClick={async () => {
                try {
                  await grokEgressCreate({ name, url, kind });
                  toast.success("已创建");
                  setOpen(false);
                  const data = await fetchGrokEgress(1, 50);
                  setItems(data.items || []);
                } catch (err) {
                  toast.error(err instanceof Error ? err.message : "创建失败");
                }
              }}
            >
              创建
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

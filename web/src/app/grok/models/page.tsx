"use client";

import { useEffect, useState } from "react";
import { Copy, LoaderCircle } from "lucide-react";
import { toast } from "sonner";

import { Card, CardContent } from "@/components/ui/card";
import { fetchGrokModels } from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";

export default function GrokModelsPage() {
  const { isCheckingAuth } = useAuthGuard(["admin"]);
  const [models, setModels] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    if (isCheckingAuth) return;
    void (async () => {
      try {
        const data = await fetchGrokModels();
        setModels((data.items || []).map((item) => item.id));
        setError("");
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
        <h1 className="mt-1 text-2xl font-semibold tracking-tight text-stone-900">模型</h1>
        <p className="mt-1 text-sm text-stone-500">当前 Grok 上游可用模型，共 {models.length} 个。</p>
      </div>

      <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
        <CardContent className="p-5">
          {loading ? (
            <LoaderCircle className="size-4 animate-spin text-stone-400" />
          ) : models.length === 0 ? (
            <p className="text-sm text-stone-400">{error || "没有可用模型"}</p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {models.map((model) => (
                <button
                  key={model}
                  type="button"
                  className="inline-flex items-center gap-1 rounded-lg border border-stone-200 bg-stone-50 px-2.5 py-1 text-xs text-stone-600 transition hover:border-indigo-200 hover:bg-indigo-50 hover:text-indigo-700"
                  onClick={() => {
                    void navigator.clipboard.writeText(model);
                    toast.success(`已复制 ${model}`);
                  }}
                >
                  <Copy className="size-3" /> {model}
                </button>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

"use client";

import { useEffect, useState } from "react";
import { Film, LoaderCircle, Save } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { request } from "@/lib/request";

import { useSettingsStore } from "../store";

export function GifModelsCard() {
  const config = useSettingsStore((state) => state.config);
  const isSavingConfig = useSettingsStore((state) => state.isSavingConfig);
  const setGifModels = useSettingsStore((state) => state.setGifModels);
  const saveConfig = useSettingsStore((state) => state.saveConfig);
  const [models, setModels] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    request
      .get<{ catalog?: { image_models?: string[] } }>("/api/model-catalog")
      .then((response) => {
        if (cancelled) {
          return;
        }
        const list = response.data?.catalog?.image_models || [];
        setModels(Array.from(new Set(list.map((item) => String(item || "").trim()).filter(Boolean))));
      })
      .catch(() => undefined)
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const selected = new Set(
    Array.isArray(config?.gif_models) ? (config?.gif_models as string[]).map((item) => String(item)) : [],
  );

  const toggle = (model: string) => {
    const next = new Set(selected);
    if (next.has(model)) {
      next.delete(model);
    } else {
      next.add(model);
    }
    setGifModels(Array.from(next));
  };

  return (
    <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
      <CardContent className="space-y-6 p-6">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div className="flex items-center gap-3">
            <div className="flex size-10 items-center justify-center rounded-xl bg-stone-100">
              <Film className="size-5 text-stone-600" />
            </div>
            <div>
              <h2 className="text-lg font-semibold tracking-tight">GIF 输出模型</h2>
              <p className="text-sm text-stone-500">
                勾选的模型允许通过 <code>output_format: &quot;gif&quot;</code> 请求动图输出；未勾选的模型请求 GIF 会被直接拒绝。
              </p>
            </div>
          </div>
          <Badge variant={selected.size ? "success" : "secondary"} className="w-fit rounded-md px-2.5 py-1">
            已启用 {selected.size} 个
          </Badge>
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-6">
            <LoaderCircle className="size-5 animate-spin text-stone-400" />
          </div>
        ) : (
          <div className="flex flex-wrap gap-2">
            {models.map((model) => {
              const active = selected.has(model);
              return (
                <button
                  key={model}
                  type="button"
                  onClick={() => toggle(model)}
                  className={`rounded-lg border px-3 py-1.5 text-sm transition ${
                    active
                      ? "border-stone-900 bg-stone-900 text-white"
                      : "border-stone-200 bg-white text-stone-700 hover:border-stone-400"
                  }`}
                >
                  {model}
                </button>
              );
            })}
          </div>
        )}

        <div className="flex justify-end">
          <Button
            className="h-10 rounded-xl bg-stone-950 px-5 text-white hover:bg-stone-800"
            onClick={() => void saveConfig()}
            disabled={isSavingConfig}
          >
            {isSavingConfig ? <LoaderCircle className="mr-2 size-4 animate-spin" /> : <Save className="mr-2 size-4" />}
            保存
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

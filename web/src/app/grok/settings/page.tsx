"use client";

import { useEffect, useState } from "react";
import { CircleAlert, LoaderCircle, Plug, Power, Save } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { fetchGrokSettings, saveGrokSettings } from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";

type GrokSettings = {
  base_url: string;
  username: string;
  api_key_masked: string;
  configured: boolean;
  auto_tunnel?: boolean;
  ssh_host?: string;
  ssh_remote_port?: number;
  ssh_local_port?: number;
  tunnel_running?: boolean;
};

export default function GrokSettingsPage() {
  const { isCheckingAuth } = useAuthGuard(["admin"]);
  const [settings, setSettings] = useState<GrokSettings | null>(null);
  const [password, setPassword] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const load = async () => {
    try {
      const data = await fetchGrokSettings();
      setSettings(data.settings);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "读取失败");
    }
  };

  useEffect(() => {
    if (!isCheckingAuth) void load();
  }, [isCheckingAuth]);

  const patch = (key: keyof GrokSettings, value: string | number | boolean) => {
    setSettings((prev) => (prev ? { ...prev, [key]: value } : prev));
  };

  const save = async () => {
    if (!settings) return;
    setSaving(true);
    try {
      const payload: Record<string, unknown> = {
        base_url: settings.base_url,
        username: settings.username,
        auto_tunnel: Boolean(settings.auto_tunnel),
        ssh_host: settings.ssh_host,
        ssh_remote_port: Number(settings.ssh_remote_port) || 8002,
        ssh_local_port: Number(settings.ssh_local_port) || 8002,
      };
      if (password) payload.password = password;
      if (apiKey) payload.api_key = apiKey;
      const data = await saveGrokSettings(payload);
      setSettings(data.settings);
      setPassword("");
      setApiKey("");
      setMessage("已保存");
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  if (!settings) {
    return (
      <div className="pt-6 text-sm text-stone-400">
        {error ? <span className="text-rose-500">{error}</span> : <LoaderCircle className="size-4 animate-spin" />}
      </div>
    );
  }

  return (
    <div className="space-y-5 pt-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-stone-900">Grok 连接设置</h1>
        <p className="mt-1 text-sm text-stone-500">
          grok2api 跑在 VPS 上且只监听回环地址，页面通过 SSH 隧道访问它。
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
      {message ? (
        <Card className="rounded-2xl border-emerald-200 bg-emerald-50/70">
          <CardContent className="p-4 text-sm text-emerald-700">{message}</CardContent>
        </Card>
      ) : null}

      <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
        <CardContent className="space-y-4 p-5">
          <div className="text-sm font-semibold text-stone-900">网关与凭据</div>
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="gk-base">Base URL</Label>
              <Input id="gk-base" value={settings.base_url} onChange={(e) => patch("base_url", e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="gk-user">管理员账号</Label>
              <Input id="gk-user" value={settings.username} onChange={(e) => patch("username", e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="gk-pass">管理员密码</Label>
              <Input id="gk-pass" type="password" placeholder="已配置（留空则保持不变）" onChange={(e) => setPassword(e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="gk-key">客户端密钥</Label>
              <Input
                id="gk-key"
                type="password"
                placeholder={`${settings.api_key_masked || "未配置"}（留空则保持不变）`}
                onChange={(e) => setApiKey(e.target.value)}
              />
            </div>
          </div>
        </CardContent>
      </Card>

      <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
        <CardContent className="space-y-4 p-5">
          <div className="text-sm font-semibold text-stone-900">SSH 隧道</div>
          <div className="grid gap-4 md:grid-cols-3">
            <div className="space-y-1.5">
              <Label htmlFor="gk-host">SSH 主机</Label>
              <Input id="gk-host" value={settings.ssh_host || ""} onChange={(e) => patch("ssh_host", e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="gk-rport">远端端口</Label>
              <Input
                id="gk-rport"
                value={String(settings.ssh_remote_port ?? 8002)}
                onChange={(e) => patch("ssh_remote_port", e.target.value.replace(/\D/g, ""))}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="gk-lport">本地端口</Label>
              <Input
                id="gk-lport"
                value={String(settings.ssh_local_port ?? 8002)}
                onChange={(e) => patch("ssh_local_port", e.target.value.replace(/\D/g, ""))}
              />
            </div>
          </div>
          <label className="flex items-center gap-2 text-sm text-stone-600">
            <input
              type="checkbox"
              checked={Boolean(settings.auto_tunnel)}
              onChange={(e) => patch("auto_tunnel", e.target.checked)}
            />
            自动建立隧道（隧道断了会自动重连）
          </label>
          <div className="flex items-center gap-2 text-xs text-stone-400">
            <Plug className="size-3.5" />
            当前：{settings.tunnel_running ? "隧道在线" : "隧道未建立"}
          </div>
        </CardContent>
      </Card>

      <div className="flex justify-end">
        <Button onClick={() => void save()} disabled={saving}>
          {saving ? <LoaderCircle className="size-4 animate-spin" /> : <Save className="size-4" />}
          保存
        </Button>
      </div>
    </div>
  );
}

"use client";

import { useEffect, useState } from "react";
import { CircleAlert, LoaderCircle, Plug, Power, Save } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { fetchMinimaxSettings, minimaxTunnel, saveMinimaxSettings, type MinimaxSettings } from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";

export default function MinimaxSettingsPage() {
  const { isCheckingAuth } = useAuthGuard(["admin"]);
  const [settings, setSettings] = useState<MinimaxSettings | null>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const load = async () => {
    try {
      const data = await fetchMinimaxSettings();
      setSettings(data.settings);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "读取失败");
    }
  };

  useEffect(() => {
    if (!isCheckingAuth) void load();
  }, [isCheckingAuth]);

  const patch = (key: keyof MinimaxSettings, value: string | number | boolean) => {
    setSettings((prev) => (prev ? { ...prev, [key]: value } : prev));
  };

  const save = async () => {
    if (!settings) return;
    setSaving(true);
    try {
      const data = await saveMinimaxSettings({
        base_url: settings.base_url,
        username: settings.username,
        password: settings.password,
        auto_tunnel: settings.auto_tunnel,
        ssh_host: settings.ssh_host,
        ssh_remote_port: Number(settings.ssh_remote_port) || 8095,
        ssh_local_port: Number(settings.ssh_local_port) || 18095,
      });
      setSettings(data.settings);
      setMessage("已保存");
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const tunnel = async (action: "start" | "stop") => {
    try {
      await minimaxTunnel(action);
      await load();
      setMessage(action === "start" ? "隧道已启动" : "隧道已关闭");
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "操作失败");
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
        <h1 className="text-2xl font-semibold tracking-tight text-stone-900">MiniMax 连接设置</h1>
        <p className="mt-1 text-sm text-stone-500">
          minimax2api 是独立部署的号池网关，这里只配置它的连接方式与管理员账号。
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
          <div className="text-sm font-semibold text-stone-900">网关地址</div>
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="mm-base">Base URL</Label>
              <Input id="mm-base" value={settings.base_url} onChange={(e) => patch("base_url", e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="mm-user">管理员账号</Label>
              <Input id="mm-user" value={settings.username} onChange={(e) => patch("username", e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="mm-pass">管理员密码</Label>
              <Input
                id="mm-pass"
                type="password"
                placeholder={settings.has_password ? "已配置（留空则保持不变）" : "未配置"}
                onChange={(e) => patch("password", e.target.value)}
              />
            </div>
          </div>
        </CardContent>
      </Card>

      <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
        <CardContent className="space-y-4 p-5">
          <div className="text-sm font-semibold text-stone-900">SSH 隧道</div>
          <p className="text-xs text-stone-400">
            minimax2api 默认只监听 VPS 上的 127.0.0.1，隧道不通时按需自动拉起一条。
          </p>
          <div className="grid gap-4 md:grid-cols-3">
            <div className="space-y-1.5">
              <Label htmlFor="mm-host">SSH 主机</Label>
              <Input id="mm-host" value={settings.ssh_host} onChange={(e) => patch("ssh_host", e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="mm-rport">远端端口</Label>
              <Input
                id="mm-rport"
                value={String(settings.ssh_remote_port)}
                onChange={(e) => patch("ssh_remote_port", e.target.value.replace(/\D/g, ""))}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="mm-lport">本地端口</Label>
              <Input
                id="mm-lport"
                value={String(settings.ssh_local_port)}
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
            自动建立隧道
          </label>
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" variant="outline" onClick={() => void tunnel("start")}>
              <Plug className="size-4" />启动隧道
            </Button>
            <Button size="sm" variant="outline" onClick={() => void tunnel("stop")}>
              <Power className="size-4" />关闭隧道
            </Button>
            <span className="text-xs text-stone-400">
              当前：{settings.tunnel_running ? "隧道在线" : "隧道未建立"}
            </span>
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

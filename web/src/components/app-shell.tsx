"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Activity, BookOpen, Boxes, FileText, Film, Gauge, Home, Image, KeyRound, Layers, ShieldCheck, Sparkles, UserPlus, Users } from "lucide-react";

import { ProductSwitcher } from "@/components/product-switcher";
import { TopNav } from "@/components/top-nav";
import { cn } from "@/lib/utils";

function GrokNav() {
  const pathname = usePathname() || "";
  const items = [
    { href: "/grok", label: "仪表盘", icon: Gauge, match: () => pathname === "/grok" },
    { href: "/grok/register", label: "注册", icon: UserPlus, match: () => pathname.startsWith("/grok/register") },
    { href: "/grok/accounts", label: "账号", icon: Users, match: () => pathname.startsWith("/grok/accounts") },
    { href: "/grok/keys", label: "密钥", icon: KeyRound, match: () => pathname.startsWith("/grok/keys") },
    { href: "/grok/models", label: "模型", icon: Layers, match: () => pathname.startsWith("/grok/models") },
    { href: "/grok/media", label: "图库", icon: Image, match: () => pathname.startsWith("/grok/media") },
    { href: "/grok/videos", label: "视频库", icon: Film, match: () => pathname.startsWith("/grok/videos") },
    { href: "/grok/audits", label: "请求审计", icon: FileText, match: () => pathname.startsWith("/grok/audits") },
    { href: "/grok/guard", label: "质量守护", icon: ShieldCheck, match: () => pathname.startsWith("/grok/guard") },
    { href: "/grok/playground", label: "创作控制台", icon: Sparkles, match: () => pathname.startsWith("/grok/playground") },
    { href: "/grok/live", label: "实时控制台", icon: Activity, match: () => pathname.startsWith("/grok/live") },
    { href: "/grok/docs", label: "文档", icon: BookOpen, match: () => pathname.startsWith("/grok/docs") },
  ];
  return (
    <aside className="fixed inset-y-0 left-0 z-40 hidden w-60 flex-col bg-[#121826] text-slate-200 md:flex">
      <div className="flex h-16 items-center gap-2 px-5">
        <div className="flex size-9 items-center justify-center rounded-xl bg-indigo-500/90 text-white">
          <Boxes className="size-5" />
        </div>
        <div className="text-sm font-semibold tracking-tight">Grok 反代</div>
      </div>
      <nav className="flex-1 space-y-1 px-3 pt-2">
        {items.map((item) => {
          const Icon = item.icon;
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm transition-colors",
                item.match() ? "bg-indigo-500 text-white shadow-sm" : "text-slate-300 hover:bg-white/10",
              )}
            >
              <Icon className="size-4" />
              {item.label}
            </Link>
          );
        })}
        <Link
          href="/"
          className="flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm text-slate-400 transition-colors hover:bg-white/10"
        >
          <Home className="size-4" />
          返回首页
        </Link>
      </nav>
      <div className="px-5 pb-5 text-[11px] text-slate-500">v1.9.0-ng</div>
    </aside>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname() || "/";
  const isLogin = pathname.startsWith("/login");
  if (isLogin) {
    return <main className="min-h-screen bg-background text-foreground">{children}</main>;
  }
  const isHome = pathname === "/";
  const isGrok = pathname.startsWith("/grok");
  return (
    <>
      {isHome ? null : isGrok ? <GrokNav /> : <TopNav />}
      <main
        className={cn(
          "min-h-screen overflow-x-hidden bg-background text-foreground",
          !isHome && "md:pl-60",
        )}
      >
        <ProductSwitcher />
        <div
          className={cn(
            "mx-auto box-border w-full px-4 pb-6 pt-2 sm:px-6 lg:px-8",
            isHome ? "max-w-[1600px]" : "max-w-[1440px]",
          )}
        >
          {children}
        </div>
      </main>
    </>
  );
}

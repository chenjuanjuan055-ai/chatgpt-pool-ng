"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  Activity,
  Bug,
  Image as ImageIcon,
  Images,
  LayoutGrid,
  LogOut,
  Menu,
  ScrollText,
  Settings,
  Sparkles,
  Users,
} from "lucide-react";
import { usePathname, useRouter } from "next/navigation";

import { HeaderActions } from "@/components/header-actions";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Sheet,
  SheetClose,
  SheetContent,
  SheetFooter,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import webConfig from "@/constants/common-env";
import { fetchThirdPartyApps, type ThirdPartyAppsSettings } from "@/lib/api";
import { getValidatedAuthSession } from "@/lib/auth-session";
import { cn } from "@/lib/utils";
import { clearStoredAuthSession, type StoredAuthSession } from "@/store/auth";

const adminNavItems = [
  { href: "/image", label: "生图", icon: ImageIcon },
  { href: "/accounts", label: "号池管理", icon: Users },
  { href: "/image-manager", label: "图片管理", icon: Images },
  { href: "/logs", label: "日志管理", icon: ScrollText },
  { href: "/monitor", label: "监控", icon: Activity },
  { href: "/debug", label: "调试", icon: Bug },
  { href: "/settings", label: "设置", icon: Settings },
];

const userNavItems = [{ href: "/image", label: "画图", icon: ImageIcon }];

function buildThirdPartyHref(appUrl: string, baseUrl: string, apiKey: string) {
  const url = appUrl.trim();
  try {
    const target = new URL(url);
    target.searchParams.set("apiKey", apiKey);
    target.searchParams.set("baseUrl", baseUrl);
    return target.toString();
  } catch {
    return `${url}${url.includes("?") ? "&" : "?"}apiKey=${encodeURIComponent(apiKey)}&baseUrl=${encodeURIComponent(baseUrl)}`;
  }
}

export function TopNav() {
  const pathname = usePathname();
  const router = useRouter();
  const [session, setSession] = useState<StoredAuthSession | null | undefined>(undefined);
  const [thirdPartyApps, setThirdPartyApps] = useState<ThirdPartyAppsSettings | null>(null);
  const [isCanvasDialogOpen, setIsCanvasDialogOpen] = useState(false);

  useEffect(() => {
    let active = true;
    const load = async () => {
      if (pathname === "/login") {
        if (active) setSession(null);
        return;
      }
      const storedSession = await getValidatedAuthSession();
      if (active) setSession(storedSession);
    };
    void load();
    return () => {
      active = false;
    };
  }, [pathname]);

  useEffect(() => {
    if (!session) {
      setThirdPartyApps(null);
      return;
    }
    let active = true;
    const load = async () => {
      try {
        const data = await fetchThirdPartyApps();
        if (active) setThirdPartyApps(data.third_party_apps);
      } catch {
        if (active) setThirdPartyApps(null);
      }
    };
    const reload = () => void load();
    void load();
    window.addEventListener("third-party-apps-updated", reload);
    return () => {
      active = false;
      window.removeEventListener("third-party-apps-updated", reload);
    };
  }, [session]);

  const handleLogout = async () => {
    await clearStoredAuthSession();
    router.replace("/login");
  };

  if (pathname === "/login" || session === undefined || !session) {
    return null;
  }

  const navItems = session.role === "admin" ? adminNavItems : userNavItems;
  const roleLabel = session.role === "admin" ? "管理员" : "普通用户";
  const displayName = session.name.trim() || roleLabel;
  const baseUrl = webConfig.apiUrl.replace(/\/$/, "") || window.location.origin;
  const canvas = thirdPartyApps?.infinite_canvas;
  const canvasHref = canvas?.enabled && canvas.url.trim() ? buildThirdPartyHref(canvas.url, baseUrl, session.key) : "";
  const canvasDisplayHref = canvasHref ? decodeURIComponent(canvasHref) : "";

  const isActive = (href: string) => pathname === href || pathname.startsWith(`${href}/`);

  const handleCanvasOpen = () => {
    if (canvasHref) setIsCanvasDialogOpen(true);
  };
  const confirmCanvasOpen = () => {
    if (canvasHref) window.open(canvasHref, "_blank", "noopener,noreferrer");
    setIsCanvasDialogOpen(false);
  };

  const navLinkClass = (active: boolean) =>
    cn(
      "flex items-center gap-2.5 rounded-xl px-3 py-2 text-sm font-medium transition-colors",
      active
        ? "bg-sidebar-primary text-sidebar-primary-foreground shadow-sm"
        : "text-sidebar-foreground/75 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
    );

  return (
    <>
      <header className="sticky top-0 z-30 flex h-14 items-center gap-2 border-b border-border/70 bg-background/85 px-4 backdrop-blur md:hidden">
        <Sheet>
          <SheetTrigger className="inline-flex size-9 items-center justify-center rounded-lg text-foreground/80 transition hover:bg-accent hover:text-foreground">
            <Menu className="size-5" />
            <span className="sr-only">打开导航</span>
          </SheetTrigger>
          <SheetContent side="left" className="w-72 bg-sidebar text-sidebar-foreground">
            <SheetHeader>
              <SheetTitle className="flex items-center gap-2 text-sidebar-foreground">
                <span className="grid size-8 place-items-center rounded-xl bg-sidebar-primary text-sidebar-primary-foreground">
                  <Sparkles className="size-4" />
                </span>
                ChatGPT 号池
              </SheetTitle>
            </SheetHeader>
            <nav className="mt-6 flex flex-col gap-1">
              {canvasHref ? (
                <SheetClose asChild>
                  <button type="button" className={navLinkClass(false)} onClick={handleCanvasOpen}>
                    <LayoutGrid className="size-4" /> 无限画布
                  </button>
                </SheetClose>
              ) : null}
              {navItems.map((item) => {
                const Icon = item.icon;
                return (
                  <SheetClose asChild key={item.href}>
                    <Link href={item.href} className={navLinkClass(isActive(item.href))}>
                      <Icon className="size-4" /> {item.label}
                    </Link>
                  </SheetClose>
                );
              })}
            </nav>
            <SheetFooter>
              <button
                type="button"
                className="flex items-center gap-2 rounded-xl border border-sidebar-border px-3 py-2 text-left text-sm text-sidebar-foreground/80"
                onClick={() => void handleLogout()}
              >
                <LogOut className="size-4" /> 退出
              </button>
            </SheetFooter>
          </SheetContent>
        </Sheet>
        <Link href="/image" className="text-[15px] font-semibold tracking-tight">
          ChatGPT 号池
        </Link>
        <HeaderActions className="ml-auto" />
      </header>

      <aside className="fixed inset-y-0 left-0 z-40 hidden w-60 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground md:flex">
        <div className="flex h-16 items-center gap-2.5 px-5">
          <span className="grid size-8 place-items-center rounded-xl bg-sidebar-primary text-sidebar-primary-foreground shadow-sm">
            <Sparkles className="size-4" />
          </span>
          <span className="text-[15px] font-semibold tracking-tight">ChatGPT 号池</span>
        </div>
        <nav className="flex-1 space-y-1 overflow-y-auto px-3 py-2">
          {canvasHref ? (
            <button type="button" className={cn(navLinkClass(false), "w-full")} onClick={handleCanvasOpen}>
              <LayoutGrid className="size-4" /> 无限画布
            </button>
          ) : null}
          {navItems.map((item) => {
            const Icon = item.icon;
            return (
              <Link key={item.href} href={item.href} className={navLinkClass(isActive(item.href))}>
                <Icon className="size-4" /> {item.label}
              </Link>
            );
          })}
        </nav>
        <div className="mx-3 mb-2 h-8 rounded-lg" aria-hidden="true" />
        <div className="border-t border-sidebar-border px-4 py-4">
          <div className="mb-3 flex items-center justify-between gap-2">
            <span className="truncate text-xs text-sidebar-foreground/70">
              {roleLabel} · {displayName}
            </span>
            <button
              type="button"
              className="rounded-lg p-1.5 text-sidebar-foreground/60 transition hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
              onClick={() => void handleLogout()}
              aria-label="退出"
            >
              <LogOut className="size-4" />
            </button>
          </div>
          <HeaderActions className="[&_a]:text-sidebar-foreground/70 [&_a:hover]:text-sidebar-foreground" />
        </div>
      </aside>

      <Dialog open={isCanvasDialogOpen} onOpenChange={setIsCanvasDialogOpen}>
        <DialogContent showCloseButton={false} className="rounded-2xl p-6">
          <DialogHeader className="gap-2">
            <DialogTitle>跳转到三方应用</DialogTitle>
            <DialogDescription className="text-sm leading-6">
              该入口仅供个人测试使用，建议自行本机部署后再长期使用。跳转地址会默认带上本项目地址和当前密钥，用于自动填充连接信息；如果不放心，可以取消后手动前往应用并自行输入。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <div className="text-xs font-medium text-muted-foreground">完整跳转地址</div>
            <div className="max-h-28 overflow-auto break-all rounded-xl border bg-muted px-3 py-2 font-mono text-xs leading-5">
              {canvasDisplayHref}
            </div>
          </div>
          <DialogFooter className="pt-2">
            <DialogClose asChild>
              <Button type="button" variant="outline" className="rounded-xl">
                取消
              </Button>
            </DialogClose>
            <Button type="button" className="rounded-xl" onClick={confirmCanvasOpen}>
              继续跳转
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

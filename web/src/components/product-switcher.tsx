"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

const GPT_PAGES = ["/accounts", "/image", "/image-manager", "/monitor", "/debug", "/logs", "/settings"];

const PRODUCTS = [
  { key: "home", label: "默认首页", href: "/", match: (p: string) => p === "/" },
  { key: "gpt", label: "GPT", href: "/accounts", match: (p: string) => GPT_PAGES.some((page) => p.startsWith(page)) },
  { key: "grok", label: "Grok", href: "/grok", match: (p: string) => p.startsWith("/grok") },
  { key: "deepseek", label: "DeepSeek", href: "/deepseek", match: (p: string) => p.startsWith("/deepseek") },
  { key: "trae", label: "Trae", href: "/trae", match: (p: string) => p.startsWith("/trae") },
  { key: "qoder", label: "Qoder", href: "/qoder", match: (p: string) => p.startsWith("/qoder") },
  { key: "workbuddy", label: "WorkBuddy", href: "/workbuddy", match: (p: string) => p.startsWith("/workbuddy") },
];

const ENABLED = new Set(["home", "gpt", "grok"]);

export function ProductSwitcher() {
  const pathname = usePathname() || "/";
  if (pathname.startsWith("/login")) {
    return null;
  }
  return (
    <div className="border-b border-stone-200/70 bg-white/70 backdrop-blur md:pl-60">
      <div className="mx-auto flex w-full max-w-[1440px] items-center gap-1 overflow-x-auto px-4 py-2 sm:px-6 lg:px-8">
        {PRODUCTS.map((product) =>
          ENABLED.has(product.key) ? (
            <Link
              key={product.key}
              href={product.href}
              className={cn(
                "shrink-0 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors",
                product.match(pathname)
                  ? "bg-indigo-50 text-indigo-700"
                  : "text-stone-500 hover:bg-stone-100 hover:text-stone-800",
              )}
            >
              {product.label}
            </Link>
          ) : (
            <span
              key={product.key}
              title="未接入"
              className="shrink-0 cursor-not-allowed rounded-lg px-3 py-1.5 text-sm font-medium text-stone-300"
            >
              {product.label}
            </span>
          ),
        )}
      </div>
    </div>
  );
}

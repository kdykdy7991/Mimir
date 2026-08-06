"use client";

import {
  Activity,
  BookOpenText,
  Boxes,
  ChevronRight,
  FileText,
  Gauge,
  Menu,
  Search,
  Sparkles,
  Waypoints,
  X,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { BrandMark } from "@/components/brand/brand-mark";

type NavigationItem = {
  href: string;
  icon: LucideIcon;
  label: string;
};

type NavigationGroup = {
  items: NavigationItem[];
  label: string;
};

const navigation: NavigationGroup[] = [
  {
    label: "工作台",
    items: [{ href: "/overview", icon: Gauge, label: "系统总览" }],
  },
  {
    label: "知识管理",
    items: [
      { href: "/collections", icon: Boxes, label: "知识库" },
      { href: "/documents", icon: FileText, label: "全部文档" },
    ],
  },
  {
    label: "检索实验",
    items: [
      { href: "/playground", icon: Sparkles, label: "检索调试" },
      { href: "/traces", icon: Waypoints, label: "链路追踪" },
    ],
  },
];

const pageTitles = new Map(
  navigation.flatMap((group) =>
    group.items.map((item) => [item.href, item.label] as const),
  ),
);

function isRouteActive(pathname: string, href: string) {
  return pathname === href || pathname.startsWith(`${href}/`);
}

function SidebarContent({
  pathname,
  onNavigate,
}: {
  pathname: string;
  onNavigate?: () => void;
}) {
  return (
    <div className="flex h-full flex-col">
      <div className="flex h-[var(--topbar-height)] items-center gap-3 px-5">
        <BrandMark />
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold tracking-[-0.015em]">
            SKDY RAG
          </p>
          <p className="truncate text-[0.6875rem] text-muted-foreground">
            Retrieval workspace
          </p>
        </div>
      </div>

      <nav aria-label="主导航" className="flex-1 overflow-y-auto px-3 py-5">
        {navigation.map((group, groupIndex) => (
          <div className={groupIndex === 0 ? "" : "mt-7"} key={group.label}>
            <p className="mb-2 px-3 text-[0.6875rem] font-semibold tracking-[0.14em] text-muted-foreground/80 uppercase">
              {group.label}
            </p>
            <div className="space-y-1">
              {group.items.map((item) => {
                const active = isRouteActive(pathname, item.href);
                const Icon = item.icon;

                return (
                  <Link
                    aria-current={active ? "page" : undefined}
                    className={`group relative flex min-h-10 items-center gap-3 rounded-md px-3 text-sm font-medium transition-colors duration-[var(--transition-fast)] ${
                      active
                        ? "bg-primary/10 text-primary"
                        : "text-muted-foreground hover:bg-surface-muted hover:text-foreground"
                    }`}
                    href={item.href}
                    key={item.href}
                    onClick={() => onNavigate?.()}
                  >
                    {active ? (
                      <span className="absolute inset-y-2 left-0 w-0.5 rounded-full bg-primary" />
                    ) : null}
                    <Icon aria-hidden="true" className="size-[1.125rem]" />
                    <span className="flex-1">{item.label}</span>
                    <ChevronRight
                      aria-hidden="true"
                      className={`size-3.5 transition-all ${
                        active
                          ? "translate-x-0 opacity-70"
                          : "-translate-x-1 opacity-0 group-hover:translate-x-0 group-hover:opacity-60"
                      }`}
                    />
                  </Link>
                );
              })}
            </div>
          </div>
        ))}
      </nav>

      <div className="p-3">
        <div className="rounded-lg border border-border bg-surface-muted/65 p-3.5">
          <div className="flex items-center gap-2 text-xs font-medium">
            <span className="size-2 rounded-full bg-warning shadow-[0_0_0_4px_color-mix(in_srgb,var(--warning)_12%,transparent)]" />
            API v0.2 已接入
          </div>
          <p className="mt-2 text-[0.6875rem] leading-5 text-muted-foreground">
            RAG MCP Server 控制台
          </p>
        </div>
      </div>
    </div>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [mobileOpen, setMobileOpen] = useState(false);

  const currentTitle = useMemo(() => {
    const exact = pageTitles.get(pathname);
    if (exact) return exact;

    const parent = [...pageTitles.entries()].find(([href]) =>
      pathname.startsWith(`${href}/`),
    );
    return parent?.[1] ?? "SKDY RAG";
  }, [pathname]);

  useEffect(() => {
    if (!mobileOpen) return;

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMobileOpen(false);
    };
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKeyDown);

    return () => {
      document.body.style.overflow = "";
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [mobileOpen]);

  return (
    <div className="min-h-screen lg:pl-[var(--sidebar-width)]">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-[var(--sidebar-width)] border-r border-border bg-surface/95 backdrop-blur-xl lg:block">
        <SidebarContent pathname={pathname} />
      </aside>

      {mobileOpen ? (
        <div className="fixed inset-0 z-50 lg:hidden">
          <button
            aria-label="关闭导航"
            className="absolute inset-0 bg-foreground/20 backdrop-blur-sm"
            onClick={() => setMobileOpen(false)}
            type="button"
          />
          <aside
            aria-label="移动端导航"
            className="relative h-full w-[min(86vw,19rem)] border-r border-border bg-surface-raised shadow-lg"
            id="mobile-navigation"
          >
            <button
              aria-label="关闭导航"
              className="absolute top-5 right-4 z-10 grid size-8 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-surface-muted hover:text-foreground"
              onClick={() => setMobileOpen(false)}
              type="button"
            >
              <X aria-hidden="true" className="size-4" />
            </button>
            <SidebarContent
              onNavigate={() => setMobileOpen(false)}
              pathname={pathname}
            />
          </aside>
        </div>
      ) : null}

      <header className="sticky top-0 z-20 flex h-[var(--topbar-height)] items-center gap-3 border-b border-border bg-background/78 px-[var(--space-page-inline)] backdrop-blur-xl">
        <button
          aria-controls="mobile-navigation"
          aria-expanded={mobileOpen}
          aria-label="打开导航"
          className="grid size-9 place-items-center rounded-md border border-border bg-surface text-muted-foreground shadow-sm transition-colors hover:text-foreground lg:hidden"
          onClick={() => setMobileOpen(true)}
          type="button"
        >
          <Menu aria-hidden="true" className="size-[1.125rem]" />
        </button>

        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold tracking-[-0.01em]">
            {currentTitle}
          </p>
          <p className="hidden truncate text-[0.6875rem] text-muted-foreground sm:block">
            RAG MCP Server 管理与检索调试控制台
          </p>
        </div>

        <button
          aria-label="搜索，功能即将开放"
          className="hidden h-9 min-w-52 items-center gap-2 rounded-md border border-border bg-surface px-3 text-xs text-muted-foreground shadow-sm transition-colors hover:border-border-strong hover:text-foreground md:flex"
          type="button"
        >
          <Search aria-hidden="true" className="size-3.5" />
          <span className="flex-1 text-left">搜索知识库</span>
          <kbd className="rounded border border-border bg-surface-muted px-1.5 py-0.5 font-mono text-[0.625rem]">
            ⌘ K
          </kbd>
        </button>

        <div
          aria-label="API 状态：v0.2 已接入"
          className="flex h-9 items-center gap-2 rounded-md border border-border bg-surface px-3 text-xs font-medium text-muted-foreground shadow-sm"
          role="status"
        >
          <Activity aria-hidden="true" className="size-3.5 text-success" />
          <span className="hidden sm:inline">v0.2 已接入</span>
        </div>
      </header>

      <main>{children}</main>

      <footer className="px-[var(--space-page-inline)] pb-5 text-center text-[0.6875rem] text-muted-foreground">
        <span className="inline-flex items-center gap-1.5">
          <BookOpenText aria-hidden="true" className="size-3" />
          SKDY RAG MCP Server · Web Console
        </span>
      </footer>
    </div>
  );
}

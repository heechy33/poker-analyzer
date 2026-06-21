"use client";

import {
  BarChart3,
  LayoutDashboard,
  LogOut,
  Menu,
  TrendingDown,
  Upload,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Separator } from "@/components/ui/separator";
import { createClient } from "@/lib/supabase/client";
import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/upload", label: "Upload", icon: Upload },
  { href: "/hands", label: "Hands", icon: BarChart3 },
  { href: "/leaks", label: "Leaks", icon: TrendingDown },
] as const;

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export function AppShell({
  email,
  children,
}: {
  email: string;
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const router = useRouter();
  const [mobileOpen, setMobileOpen] = useState(false);

  async function handleSignOut() {
    const supabase = createClient();
    await supabase.auth.signOut();
    router.push("/login");
    router.refresh();
  }

  const navLink = (href: string, label: string, Icon: React.ComponentType<{ className?: string }>) => (
    <Link
      key={href}
      href={href}
      onClick={() => setMobileOpen(false)}
      className={cn(
        "flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
        pathname === href
          ? "bg-emerald-500/10 text-emerald-400"
          : "text-zinc-400 hover:bg-zinc-900 hover:text-zinc-100",
      )}
    >
      <Icon className="h-4 w-4" />
      {label}
    </Link>
  );

  return (
    <div className="min-h-screen bg-background">
      <header className="flex items-center justify-between border-b border-border px-4 py-3 md:hidden">
        <p className="text-sm font-semibold text-emerald-400">CoinPoker Analyzer</p>
        <Button variant="ghost" size="icon" onClick={() => setMobileOpen((o) => !o)} aria-label="Menu">
          <Menu className="h-5 w-5" />
        </Button>
      </header>

      {mobileOpen && (
        <nav className="flex flex-col gap-1 border-b border-border px-4 py-3 md:hidden">
          {NAV_ITEMS.map(({ href, label, icon: Icon }) => navLink(href, label, Icon))}
          <Separator className="my-2" />
          <p className="truncate px-3 text-xs text-muted-foreground">{email}</p>
          <Button variant="ghost" className="justify-start gap-2" onClick={handleSignOut}>
            <LogOut className="h-4 w-4" />
            Sign out
          </Button>
        </nav>
      )}

      <div className="flex min-h-[calc(100vh-0px)] md:min-h-screen">
        <aside className="hidden w-56 shrink-0 flex-col border-r border-border bg-card md:flex">
          <div className="px-4 py-6">
            <p className="text-xs font-medium uppercase tracking-wider text-emerald-400">
              CoinPoker Analyzer
            </p>
          </div>
          <nav className="flex flex-1 flex-col gap-1 px-3">
            {NAV_ITEMS.map(({ href, label, icon: Icon }) => navLink(href, label, Icon))}
          </nav>
          <div className="mt-auto space-y-3 px-4 py-4">
            <p className="truncate text-xs text-muted-foreground">{email}</p>
            <Button variant="outline" size="sm" className="w-full gap-2" onClick={handleSignOut}>
              <LogOut className="h-4 w-4" />
              Sign out
            </Button>
            <p className="text-[10px] leading-relaxed text-muted-foreground">
              API:{" "}
              <a
                className="text-emerald-400 hover:underline"
                href={`${API_URL}/health`}
                rel="noreferrer"
                target="_blank"
              >
                {API_URL}/health
              </a>
            </p>
          </div>
        </aside>

        <div className="flex min-w-0 flex-1 flex-col">
          <header className="hidden items-center justify-end border-b border-border px-6 py-3 md:flex">
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="sm" className="max-w-[240px] truncate">
                  {email}
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem onClick={handleSignOut}>
                  <LogOut className="mr-2 h-4 w-4" />
                  Sign out
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </header>
          <main className="flex-1 p-6">{children}</main>
          <footer className="border-t border-border px-6 py-3 text-xs text-muted-foreground md:hidden">
            API:{" "}
            <a
              className="text-emerald-400 hover:underline"
              href={`${API_URL}/health`}
              rel="noreferrer"
              target="_blank"
            >
              {API_URL}/health
            </a>
          </footer>
        </div>
      </div>
    </div>
  );
}

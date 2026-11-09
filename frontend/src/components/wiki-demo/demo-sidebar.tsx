"use client";

import { HardDrive, LockKeyhole, Moon, Search, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import Link from "next/link";

import { SarjLogo } from "@/components/sarj-logo";
import { Button } from "@/components/ui/button";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarTrigger,
} from "@/components/ui/sidebar";
import { useIsClient } from "@/hooks/use-is-client";
import { useSearchShortcut } from "@/hooks/use-search-shortcut";
import { renderMimeIcon } from "@/lib/file-icons";

import {
  DEMO_FILES,
  DEMO_WORKSPACE_NAME,
  type DemoFile,
  demoHref,
} from "./demo-data";

export function DemoSidebar({
  file,
  onNavigate,
  onSearch,
}: {
  file: DemoFile;
  onNavigate: () => void;
  onSearch: () => void;
}) {
  const { resolvedTheme, setTheme } = useTheme();
  const mounted = useIsClient();
  const dark = mounted && resolvedTheme === "dark";
  const searchShortcut = useSearchShortcut();

  return (
    <Sidebar>
      <nav aria-label="Demo Drive" className="flex min-h-0 flex-1 flex-col">
        <SidebarHeader>
          <div className="flex items-center justify-between gap-2 px-2 py-1.5">
            <Link
              aria-label="Wiki demo home"
              className="focus-visible:ring-ring rounded-sm focus-visible:ring-2"
              href="/demo"
              onClick={onNavigate}
              prefetch={false}
            >
              <SarjLogo size={28} />
            </Link>
            <span className="text-muted-foreground mr-8 rounded-sm border px-1.5 py-0.5 text-xs font-medium tracking-wider uppercase md:mr-0">
              Demo
            </span>
            <SidebarTrigger className="hidden md:inline-flex" size="icon-sm" />
          </div>
          <div className="px-2 pb-1">
            <Button
              aria-label="Open search"
              className="w-full justify-start"
              onClick={onSearch}
              size="default"
              variant="outline"
            >
              <Search aria-hidden className="size-3.5" />
              <span className="flex-1 text-left">Search Drive…</span>
              <kbd className="bg-muted rounded-sm border px-1 py-0.5 font-mono text-xs">
                {searchShortcut}
              </kbd>
            </Button>
          </div>
        </SidebarHeader>
        <SidebarContent>
          <SidebarGroup>
            <div className="text-muted-foreground flex h-9 items-center gap-2 px-2 text-xs font-medium">
              <HardDrive aria-hidden className="size-3.5" />
              <span>{DEMO_WORKSPACE_NAME}</span>
            </div>
            <SidebarGroupContent>
              <SidebarMenu>
                {DEMO_FILES.map((entry) => (
                  <SidebarMenuItem key={entry.id}>
                    <SidebarMenuButton
                      asChild
                      className="min-h-9"
                      isActive={entry.id === file.id}
                      size="sm"
                    >
                      <Link
                        aria-current={entry.id === file.id ? "page" : undefined}
                        href={demoHref(entry)}
                        onClick={onNavigate}
                        prefetch={false}
                        title={entry.name}
                      >
                        <span className="flex min-w-0 flex-1 items-center gap-2">
                          <span aria-hidden className="shrink-0">
                            {renderMimeIcon(entry.mimeType, "size-4")}
                          </span>
                          <span className="truncate">{entry.name}</span>
                        </span>
                      </Link>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                ))}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        </SidebarContent>
        <div className="border-sidebar-border border-t">
          <SidebarFooter>
            <div className="flex items-center gap-2 rounded-md px-2 py-1">
              <div className="bg-sidebar-accent flex size-8 shrink-0 items-center justify-center rounded-full">
                <LockKeyhole
                  aria-hidden
                  className="text-muted-foreground size-3.5"
                />
              </div>
              <p className="min-w-0 flex-1 text-xs font-medium">
                Read-only workspace
              </p>
              <Button
                aria-label={
                  dark ? "Switch to light theme" : "Switch to dark theme"
                }
                onClick={() => setTheme(dark ? "light" : "dark")}
                size="icon-sm"
                title="Toggle theme"
                variant="ghost"
              >
                {dark ? (
                  <Sun aria-hidden className="size-4" />
                ) : (
                  <Moon aria-hidden className="size-4" />
                )}
              </Button>
            </div>
          </SidebarFooter>
        </div>
      </nav>
    </Sidebar>
  );
}

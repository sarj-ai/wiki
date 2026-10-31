"use client";

import {
  ArrowDownAZ,
  ChevronRight,
  Folder,
  HardDrive,
  LockKeyhole,
  Moon,
  Search,
  Sun,
} from "lucide-react";
import Link from "next/link";
import { useTheme } from "next-themes";
import { type CSSProperties, useState } from "react";

import { SarjLogo } from "@/components/sarj-logo";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
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
import { rowIndent } from "@/components/wiki-sidebar/layout-constants";
import { useIsClient } from "@/hooks/use-is-client";
import { renderMimeIcon } from "@/lib/file-icons";
import { cn } from "@/lib/utils";

import {
  DEMO_FILES,
  DEMO_FOLDERS,
  DEMO_SAMPLE_DESCRIPTION,
  DEMO_WORKSPACE_NAME,
  type DemoFile,
  demoHref,
} from "./demo-data";

interface DemoRowStyle extends CSSProperties {
  readonly "--demo-row-indent": string;
}

const DEMO_ROW_STYLE: Readonly<DemoRowStyle> = {
  "--demo-row-indent": `${rowIndent(1) - rowIndent(0)}px`,
};

export function DemoSidebar({
  file,
  onNavigate,
  onSearch,
}: {
  file: DemoFile;
  onNavigate: () => void;
  onSearch: () => void;
}) {
  const [closedFolders, setClosedFolders] = useState<readonly string[]>([]);
  const [sort, setSort] = useState("asc");
  const { resolvedTheme, setTheme } = useTheme();
  const mounted = useIsClient();
  const dark = mounted && resolvedTheme === "dark";
  const sortedFiles = DEMO_FILES.toSorted((first, second) => {
    const order = first.name.localeCompare(second.name);
    return sort === "asc" ? order : -order;
  });

  return (
    <Sidebar>
      <nav aria-label="Sample workspace" className="flex min-h-0 flex-1 flex-col">
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
              <kbd className="bg-muted rounded-sm border px-1 py-0.5 font-mono text-xs">⌘K</kbd>
            </Button>
          </div>
        </SidebarHeader>
        <SidebarContent>
          <SidebarGroup>
            <div className="text-muted-foreground flex h-9 items-center gap-2 px-2 text-xs font-medium">
              <HardDrive aria-hidden className="size-3.5" />
              <span className="flex-1">{DEMO_WORKSPACE_NAME}</span>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button
                    aria-label="Sort files"
                    className="size-8"
                    size="icon-sm"
                    title="Sort files"
                    variant="ghost"
                  >
                    <ArrowDownAZ aria-hidden className="size-3.5" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuRadioGroup onValueChange={setSort} value={sort}>
                    <DropdownMenuRadioItem value="asc">Name A → Z</DropdownMenuRadioItem>
                    <DropdownMenuRadioItem value="desc">Name Z → A</DropdownMenuRadioItem>
                  </DropdownMenuRadioGroup>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
            <SidebarGroupContent>
              <SidebarMenu>
                {DEMO_FOLDERS.map((folder) => {
                  const open = !closedFolders.includes(folder.id);
                  return (
                    <SidebarMenuItem key={folder.id}>
                      <SidebarMenuButton
                        aria-controls={`demo-folder-${folder.id}`}
                        aria-expanded={open}
                        className="min-h-9"
                        onClick={() =>
                          setClosedFolders((current) =>
                            open
                              ? [...current, folder.id]
                              : current.filter((id) => id !== folder.id),
                          )
                        }
                        size="sm"
                      >
                        <span className="flex min-w-0 flex-1 items-center gap-1">
                          <ChevronRight
                            aria-hidden
                            className={cn(
                              "size-3.5 shrink-0 motion-safe:transition-transform",
                              open && "rotate-90",
                            )}
                          />
                          <Folder aria-hidden className="text-icon-folder mx-1 size-4 shrink-0" />
                          <span className="truncate">{folder.label}</span>
                        </span>
                      </SidebarMenuButton>
                      <SidebarMenu hidden={!open} id={`demo-folder-${folder.id}`}>
                        {sortedFiles
                          .filter((entry) => entry.folder === folder.id)
                          .map((entry) => (
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
                                  <span
                                    className="flex min-w-0 flex-1 items-center gap-2 pl-(--demo-row-indent)"
                                    style={DEMO_ROW_STYLE}
                                  >
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
                    </SidebarMenuItem>
                  );
                })}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        </SidebarContent>
        <div className="border-sidebar-border border-t">
          <SidebarFooter>
            <div className="flex items-center gap-2 rounded-md px-2 py-1">
              <div className="bg-sidebar-accent flex size-8 shrink-0 items-center justify-center rounded-full">
                <LockKeyhole aria-hidden className="text-muted-foreground size-3.5" />
              </div>
              <div className="min-w-0 flex-1">
                <p className="text-xs font-medium">Read-only workspace</p>
                <p className="text-muted-foreground text-xs">{DEMO_SAMPLE_DESCRIPTION}</p>
              </div>
              <Button
                aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
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

"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useTheme } from "next-themes";
import { Plus, NotebookPen, Sun, Moon, LogOut, Loader2, RefreshCw, Building2 } from "lucide-react";
import { toast } from "sonner";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupAction,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
  SidebarSeparator,
} from "@/components/ui/sidebar";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { ChevronRight } from "lucide-react";
import { PageTree } from "./page-tree";
import { useAuth } from "@/components/auth-provider";
import { useWorkspaces } from "@/hooks/use-workspaces";
import { usePageTree } from "@/hooks/use-page-tree";
import { useLoading } from "@/components/loading-bar";
import type { Page, Workspace } from "@/types";
import { api } from "@/lib/api";
import { findPageById } from "@/lib/page-utils";
import { useQueryClient } from "@tanstack/react-query";
import { CommandPalette } from "@/components/search/command-palette";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import dynamic from "next/dynamic";
import data from "@emoji-mart/data";

const EmojiMartPicker = dynamic(() => import("@emoji-mart/react").then((m) => m.default), {
  ssr: false,
  loading: () => <div className="h-[350px] w-[352px]" />,
});

export function AppSidebar() {
  const { resolvedTheme, setTheme } = useTheme();
  const { user, logout } = useAuth();
  const pathname = usePathname();
  const router = useRouter();
  const queryClient = useQueryClient();
  const { start, done } = useLoading();
  const { data: workspaces = [] } = useWorkspaces();
  const { data: allPages = [], isLoading: isTreeLoading } = usePageTree();
  const [renamingPageId, setRenamingPageId] = useState<string | null>(null);
  const [deletingPageId, setDeletingPageId] = useState<string | null>(null);
  const [addingTo, setAddingTo] = useState<string | null>(null);
  const [addingChildParentId, setAddingChildParentId] = useState<string | null>(null);
  const [isSyncing, setIsSyncing] = useState(false);
  const [workspaceEmojiPickerId, setWorkspaceEmojiPickerId] = useState<string | null>(null);

  const SECTIONS_KEY = "sarj-collapsed-sections";
  const [collapsedSections, setCollapsedSections] = useState<Set<string>>(new Set());
  const [sectionsHydrated, setSectionsHydrated] = useState(false);

  // Load from localStorage after mount to avoid hydration mismatch
  useEffect(() => {
    try {
      const stored = localStorage.getItem(SECTIONS_KEY);
      if (stored) setCollapsedSections(new Set(JSON.parse(stored) as string[]));
    } catch {
      // ignore
    }
    setSectionsHydrated(true);
  }, []);

  // Persist to localStorage on change (skip initial mount)
  useEffect(() => {
    if (!sectionsHydrated) return;
    try {
      localStorage.setItem(SECTIONS_KEY, JSON.stringify([...collapsedSections]));
    } catch {
      // ignore
    }
  }, [collapsedSections, sectionsHydrated]);

  const toggleSection = useCallback((id: string) => {
    setCollapsedSections((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  async function handleSync() {
    setIsSyncing(true);
    try {
      const [, result] = await Promise.all([
        api.syncWorkspaces(),
        api.syncPages(),
      ]);
      queryClient.invalidateQueries({ queryKey: ["workspaces"] });
      queryClient.invalidateQueries({ queryKey: ["pages"] });
      const parts: string[] = [];
      if (result.added) parts.push(`${result.added} added`);
      if (result.updated) parts.push(`${result.updated} updated`);
      if (result.removed) parts.push(`${result.removed} removed`);
      toast.success(parts.length ? `Synced: ${parts.join(", ")}` : "Everything up to date");
    } catch {
      toast.error("Failed to sync");
    } finally {
      setIsSyncing(false);
    }
  }

  async function handleWorkspaceIcon(workspaceId: string, icon: string | null) {
    // Optimistic update — reflect immediately in the UI
    queryClient.setQueryData<Workspace[]>(["workspaces"], (old) =>
      old?.map((ws) => (ws.id === workspaceId ? { ...ws, icon } : ws))
    );
    try {
      await api.updateWorkspace(workspaceId, { icon });
    } catch {
      toast.error("Failed to update workspace icon");
      queryClient.invalidateQueries({ queryKey: ["workspaces"] });
    }
  }

  const activePageId = pathname?.startsWith("/docs/")
    ? decodeURIComponent(pathname.split("/")[2] ?? "")  || null
    : null;

  const privatePages = useMemo(
    () => allPages.filter((p) => p.isPrivate),
    [allPages],
  );

  const pagesByWorkspace = useMemo(() => {
    const map = new Map<string, Page[]>();
    for (const ws of workspaces) {
      map.set(ws.id, allPages.filter((p) => p.workspaceId === ws.id));
    }
    return map;
  }, [allPages, workspaces]);

  function countDescendants(page: Page): number {
    let count = 0;
    for (const child of page.children) {
      count += 1 + countDescendants(child);
    }
    return count;
  }

  const deletingPage = deletingPageId ? findPageById(deletingPageId, allPages) : null;
  const deleteChildCount = deletingPage ? countDescendants(deletingPage) : 0;

  async function handleDelete() {
    if (!deletingPageId) return;
    const pageId = deletingPageId;
    setDeletingPageId(null);
    start();
    try {
      await api.deletePage(pageId);
      queryClient.invalidateQueries({ queryKey: ["pages"] });
      if (activePageId === pageId) {
        router.push("/");
      }
    } catch {
      toast.error("Failed to delete page");
    } finally {
      done();
    }
  }

  async function handleRename(pageId: string, newTitle: string) {
    try {
      await api.updatePage(pageId, { title: newTitle });
      // Fire-and-forget: refetch tree in background
      queryClient.invalidateQueries({ queryKey: ["pages"] });
      queryClient.invalidateQueries({ queryKey: ["pages", pageId] });
    } catch {
      toast.error("Failed to rename page");
    }
  }

  async function handleChangeIcon(pageId: string, icon: string | null) {
    try {
      await api.updatePage(pageId, { icon });
      queryClient.invalidateQueries({ queryKey: ["pages"] });
      queryClient.invalidateQueries({ queryKey: ["pages", pageId] });
    } catch {
      toast.error("Failed to update icon");
    }
  }

  async function handleMove(pageId: string, newParentId: string | null, newIndex: number) {
    start();
    try {
      const sortOrder = newIndex + 0.5;
      await api.updatePage(pageId, { parentId: newParentId, sortOrder });
      await queryClient.invalidateQueries({ queryKey: ["pages"] });
    } catch {
      toast.error("Failed to move page");
    } finally {
      done();
    }
  }

  async function handleAddChild(parentId: string) {
    setAddingChildParentId(parentId);
    try {
      const parentPage = findPageById(parentId, allPages);
      const page = await api.createPage({
        title: "Untitled",
        parentId,
        workspaceId: parentPage?.workspaceId ?? undefined,
      });
      // Show rename input immediately, refetch tree in background
      setRenamingPageId(page.id);
      queryClient.invalidateQueries({ queryKey: ["pages"] });
    } catch {
      toast.error("Failed to create page");
    } finally {
      setAddingChildParentId(null);
    }
  }

  async function handleAddRootPage(workspaceId?: string) {
    setAddingTo(workspaceId ?? "no-workspace");
    try {
      const page = await api.createPage({
        title: "Untitled",
        workspaceId,
      });
      setRenamingPageId(page.id);
      queryClient.invalidateQueries({ queryKey: ["pages"] });
    } catch {
      toast.error("Failed to create page");
    } finally {
      setAddingTo(null);
    }
  }

  async function handleAddPrivatePage() {
    setAddingTo("private");
    try {
      const page = await api.createPage({ title: "Untitled" });
      setRenamingPageId(page.id);
      queryClient.invalidateQueries({ queryKey: ["pages"] });
    } catch {
      toast.error("Failed to create page");
    } finally {
      setAddingTo(null);
    }
  }

  const handleRenamingDone = useCallback(() => {
    if (renamingPageId) {
      router.push(`/docs/${renamingPageId}`);
    }
    setRenamingPageId(null);
  }, [renamingPageId, router]);

  const displayName = user?.displayName ?? user?.email ?? "";
  const initials = user?.displayName
    ? user.displayName
        .split(" ")
        .map((part) => part[0])
        .join("")
        .toUpperCase()
    : (user?.email?.[0] ?? "").toUpperCase();

  return (
    <Sidebar>
      <SidebarHeader>
        <div className="flex items-center justify-between px-2 py-1.5">
          <span className="text-sm font-semibold">Sarj Docs</span>
          <button
            type="button"
            onClick={handleSync}
            disabled={isSyncing}
            title="Sync workspaces"
            className="flex size-6 items-center justify-center rounded-md text-sidebar-foreground ring-sidebar-ring outline-hidden hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:ring-2 transition-colors"
          >
            <RefreshCw className={`size-3.5 ${isSyncing ? "animate-spin" : ""}`} />
          </button>
        </div>
        <div className="px-2">
          <CommandPalette />
        </div>
      </SidebarHeader>

      <SidebarContent>
        {/* One collapsible section per workspace */}
        {workspaces.map((workspace) => (
          <Collapsible
            key={workspace.id}
            open={!collapsedSections.has(workspace.id)}
            onOpenChange={() => toggleSection(workspace.id)}
          >
            <SidebarGroup>
              <SidebarGroupLabel asChild className="text-sm font-semibold text-sidebar-foreground">
                <div className="flex items-center">
                  <CollapsibleTrigger asChild>
                    <button
                      type="button"
                      className="mr-1 inline-flex shrink-0 items-center justify-center rounded-sm p-0.5 hover:bg-sidebar-accent transition-colors"
                      aria-label={`Toggle ${workspace.name}`}
                    >
                      <ChevronRight className={`size-3 transition-transform ${!collapsedSections.has(workspace.id) ? "rotate-90" : ""}`} />
                    </button>
                  </CollapsibleTrigger>
                  <Popover
                    open={workspaceEmojiPickerId === workspace.id}
                    onOpenChange={(open) => setWorkspaceEmojiPickerId(open ? workspace.id : null)}
                  >
                    <PopoverTrigger asChild>
                      <button
                        type="button"
                        className="mr-1.5 inline-flex shrink-0 items-center justify-center hover:opacity-70 transition-opacity"
                        title="Change icon"
                        aria-label={`Change ${workspace.name} icon`}
                      >
                        {workspace.icon ? (
                          <span className="text-xl leading-none">{workspace.icon}</span>
                        ) : (
                          <Building2 className="size-5" />
                        )}
                      </button>
                    </PopoverTrigger>
                    <PopoverContent
                      className="w-auto p-0 border-none shadow-lg"
                      side="right"
                      align="start"
                      sideOffset={8}
                      onOpenAutoFocus={(e) => e.preventDefault()}
                      onCloseAutoFocus={(e) => e.preventDefault()}
                    >
                      <EmojiMartPicker
                        data={data}
                        theme={resolvedTheme === "dark" ? "dark" : "light"}
                        onEmojiSelect={(emoji: { native: string }) => {
                          handleWorkspaceIcon(workspace.id, emoji.native);
                          setWorkspaceEmojiPickerId(null);
                        }}
                        previewPosition="none"
                        skinTonePosition="search"
                        maxFrequentRows={2}
                      />
                      {workspace.icon && (
                        <div className="border-t px-3 py-2">
                          <button
                            type="button"
                            onClick={() => {
                              handleWorkspaceIcon(workspace.id, null);
                              setWorkspaceEmojiPickerId(null);
                            }}
                            className="text-xs text-muted-foreground hover:text-foreground transition-colors"
                          >
                            Remove icon
                          </button>
                        </div>
                      )}
                    </PopoverContent>
                  </Popover>
                  <span className="truncate">{workspace.name}</span>
                </div>
              </SidebarGroupLabel>
              <SidebarGroupAction onClick={() => handleAddRootPage(workspace.id)} title={`Add page to ${workspace.name}`} disabled={addingTo !== null}>
                {addingTo === workspace.id ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />}
              </SidebarGroupAction>
              <CollapsibleContent>
                <SidebarGroupContent className="pl-5">
                  <PageTree
                    pages={pagesByWorkspace.get(workspace.id) ?? []}
                    activePageId={activePageId}
                    onAddChild={handleAddChild}
                    onDelete={setDeletingPageId}
                    onRename={handleRename}
                    onChangeIcon={handleChangeIcon}
                    onMove={handleMove}
                    renamingPageId={renamingPageId}
                    onRenamingDone={handleRenamingDone}
                    isLoading={isTreeLoading}
                    addingChildParentId={addingChildParentId}
                  />
                </SidebarGroupContent>
              </CollapsibleContent>
            </SidebarGroup>
          </Collapsible>
        ))}

        {workspaces.length > 0 && <SidebarSeparator />}

        {/* Private pages */}
        <Collapsible
          open={!collapsedSections.has("private")}
          onOpenChange={() => toggleSection("private")}
        >
          <SidebarGroup>
            <SidebarGroupLabel asChild className="text-sm font-semibold text-sidebar-foreground">
              <div className="flex items-center">
                <CollapsibleTrigger asChild>
                  <button
                    type="button"
                    className="mr-1 inline-flex shrink-0 items-center justify-center rounded-sm p-0.5 hover:bg-sidebar-accent transition-colors"
                    aria-label="Toggle private pages"
                  >
                    <ChevronRight className={`size-3 transition-transform ${!collapsedSections.has("private") ? "rotate-90" : ""}`} />
                  </button>
                </CollapsibleTrigger>
                <NotebookPen className="mr-1 size-3.5" />
                <span className="truncate">My Drafts</span>
              </div>
            </SidebarGroupLabel>
            <SidebarGroupAction onClick={handleAddPrivatePage} title="Add private page" disabled={addingTo !== null}>
              {addingTo === "private" ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />}
            </SidebarGroupAction>
            <CollapsibleContent>
              <SidebarGroupContent className="pl-5">
                <PageTree
                  pages={privatePages}
                  activePageId={activePageId}
                  onAddChild={handleAddChild}
                  onDelete={setDeletingPageId}
                  onRename={handleRename}
                  onChangeIcon={handleChangeIcon}
                  onMove={handleMove}
                  renamingPageId={renamingPageId}
                  onRenamingDone={handleRenamingDone}
                  isLoading={isTreeLoading}
                  addingChildParentId={addingChildParentId}
                />
              </SidebarGroupContent>
            </CollapsibleContent>
          </SidebarGroup>
        </Collapsible>
      </SidebarContent>

      <SidebarFooter>
        <SidebarMenu>
          <SidebarMenuItem>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <SidebarMenuButton
                  size="lg"
                  className="data-[state=open]:bg-sidebar-accent data-[state=open]:text-sidebar-accent-foreground"
                >
                  <Avatar className="size-8 rounded-lg">
                    <AvatarImage
                      src={user?.avatarUrl ?? undefined}
                      alt={displayName}
                    />
                    <AvatarFallback className="rounded-lg text-xs">
                      {initials}
                    </AvatarFallback>
                  </Avatar>
                  <div className="grid flex-1 text-left text-sm leading-tight">
                    <span className="truncate font-semibold">
                      {displayName}
                    </span>
                    <span className="truncate text-xs text-muted-foreground">
                      {user?.email}
                    </span>
                  </div>
                </SidebarMenuButton>
              </DropdownMenuTrigger>
              <DropdownMenuContent
                className="w-[--radix-dropdown-menu-trigger-width] min-w-56"
                side="top"
                align="start"
                sideOffset={4}
              >
                <DropdownMenuItem
                  onClick={() =>
                    setTheme(resolvedTheme === "dark" ? "light" : "dark")
                  }
                >
                  {resolvedTheme === "dark" ? (
                    <Sun className="mr-2 size-4" />
                  ) : (
                    <Moon className="mr-2 size-4" />
                  )}
                  {resolvedTheme === "dark" ? "Light mode" : "Dark mode"}
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem onClick={logout}>
                  <LogOut className="mr-2 size-4" />
                  Log out
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarFooter>

      <SidebarRail />

      <Dialog open={!!deletingPageId} onOpenChange={(open) => { if (!open) setDeletingPageId(null); }}>
        <DialogContent showCloseButton={false} className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Delete page?</DialogTitle>
            <DialogDescription>
              {deleteChildCount > 0
                ? `This will permanently delete "${deletingPage?.title}" and ${deleteChildCount} child page${deleteChildCount > 1 ? "s" : ""}. This action cannot be undone.`
                : `This will permanently delete "${deletingPage?.title}". This action cannot be undone.`}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeletingPageId(null)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={handleDelete}>
              Delete{deleteChildCount > 0 ? ` (${deleteChildCount + 1} pages)` : ""}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Sidebar>
  );
}

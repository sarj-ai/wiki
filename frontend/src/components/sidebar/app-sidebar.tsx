"use client";

import { useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useTheme } from "next-themes";
import { Plus, Lock, Sun, Moon, LogOut, Loader2 } from "lucide-react";
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
import { WorkspaceSwitcher } from "./workspace-switcher";
import { PageTree } from "./page-tree";
import { useAuth } from "@/components/auth-provider";
import { useWorkspaces } from "@/hooks/use-workspaces";
import { usePageTree } from "@/hooks/use-page-tree";
import { useLoading } from "@/components/loading-bar";
import type { Page, Workspace } from "@/types";
import { api } from "@/lib/api";
import { findPageById } from "@/lib/page-utils";
import { useQueryClient } from "@tanstack/react-query";

export function AppSidebar() {
  const { resolvedTheme, setTheme } = useTheme();
  const { user, logout } = useAuth();
  const pathname = usePathname();
  const router = useRouter();
  const queryClient = useQueryClient();
  const { start, done, isLoading: isGlobalLoading } = useLoading();
  const { data: workspaces = [] } = useWorkspaces();
  const { data: allPages = [], isLoading: isTreeLoading } = usePageTree();
  const [activeWorkspace, setActiveWorkspace] = useState<Workspace | null>(null);
  const [renamingPageId, setRenamingPageId] = useState<string | null>(null);
  const [deletingPageId, setDeletingPageId] = useState<string | null>(null);

  // Set initial workspace once loaded
  if (workspaces.length > 0 && !activeWorkspace) {
    setActiveWorkspace(workspaces[0]);
  }

  const activePageId = pathname?.startsWith("/docs/")
    ? pathname.split("/")[2] ?? null
    : null;

  const workspacePages = activeWorkspace
    ? allPages.filter((p) => p.workspaceId === activeWorkspace.id)
    : allPages.filter((p) => !p.isPrivate);

  const privatePages = allPages.filter((p) => p.isPrivate);

  function handlePageSelect(_pageId: string) {
    // Navigation is handled by Link in PageTreeItem.
  }

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
    start();
    try {
      await api.updatePage(pageId, { title: newTitle });
      queryClient.invalidateQueries({ queryKey: ["pages"] });
      queryClient.invalidateQueries({ queryKey: ["pages", pageId] });
    } catch {
      toast.error("Failed to rename page");
    } finally {
      done();
    }
  }

  async function handleChangeIcon(pageId: string, icon: string | null) {
    start();
    try {
      await api.updatePage(pageId, { icon });
      queryClient.invalidateQueries({ queryKey: ["pages"] });
      queryClient.invalidateQueries({ queryKey: ["pages", pageId] });
    } catch {
      toast.error("Failed to update icon");
    } finally {
      done();
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
    start();
    try {
      const parentPage = findPageById(parentId, allPages);
      const page = await api.createPage({
        title: "Untitled",
        parentId,
        workspaceId: parentPage?.workspaceId ?? undefined,
      });
      await queryClient.invalidateQueries({ queryKey: ["pages"] });
      setRenamingPageId(page.id);
    } catch {
      toast.error("Failed to create page");
    } finally {
      done();
    }
  }

  async function handleAddRootPage() {
    start();
    try {
      const page = await api.createPage({
        title: "Untitled",
        workspaceId: activeWorkspace?.id,
      });
      await queryClient.invalidateQueries({ queryKey: ["pages"] });
      setRenamingPageId(page.id);
    } catch {
      toast.error("Failed to create page");
    } finally {
      done();
    }
  }

  async function handleAddPrivatePage() {
    start();
    try {
      const page = await api.createPage({ title: "Untitled" });
      await queryClient.invalidateQueries({ queryKey: ["pages"] });
      setRenamingPageId(page.id);
    } catch {
      toast.error("Failed to create page");
    } finally {
      done();
    }
  }

  const displayName = user?.displayName ?? user?.email ?? "";
  const initials = user?.displayName
    ? user.displayName
        .split(" ")
        .map((n) => n[0])
        .join("")
        .toUpperCase()
    : (user?.email?.[0] ?? "").toUpperCase();

  return (
    <Sidebar>
      <SidebarHeader>
        {workspaces.length > 0 && activeWorkspace && (
          <WorkspaceSwitcher
            workspaces={workspaces}
            activeWorkspace={activeWorkspace}
            onWorkspaceChange={setActiveWorkspace}
          />
        )}
        {workspaces.length === 0 && (
          <div className="px-2 py-1.5 text-sm font-semibold">Sarj Docs</div>
        )}
      </SidebarHeader>

      <SidebarContent>
        {/* Workspace pages — only show when workspaces exist */}
        {activeWorkspace && (
          <>
            <SidebarGroup>
              <SidebarGroupLabel>Pages</SidebarGroupLabel>
              <SidebarGroupAction onClick={handleAddRootPage} title="Add page" disabled={isGlobalLoading}>
                {isGlobalLoading ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />}
              </SidebarGroupAction>
              <SidebarGroupContent>
                <PageTree
                  pages={workspacePages}
                  activePageId={activePageId}
                  onPageSelect={handlePageSelect}
                  onAddChild={handleAddChild}
                  onDelete={setDeletingPageId}
                  onRename={handleRename}
                  onChangeIcon={handleChangeIcon}
                  onMove={handleMove}
                  renamingPageId={renamingPageId}
                  onRenamingDone={() => {
                    if (renamingPageId) {
                      router.push(`/docs/${renamingPageId}`);
                    }
                    setRenamingPageId(null);
                  }}
                  loading={isTreeLoading}
                />
              </SidebarGroupContent>
            </SidebarGroup>

            <SidebarSeparator />
          </>
        )}

        {/* Private pages */}
        <SidebarGroup>
          <SidebarGroupLabel>
            <Lock className="mr-1 size-3" />
            Private
          </SidebarGroupLabel>
          <SidebarGroupAction onClick={handleAddPrivatePage} title="Add private page" disabled={isGlobalLoading}>
            {isGlobalLoading ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />}
          </SidebarGroupAction>
          <SidebarGroupContent>
            <PageTree
              pages={privatePages}
              activePageId={activePageId}
              onPageSelect={handlePageSelect}
              onAddChild={handleAddChild}
              onDelete={setDeletingPageId}
              onRename={handleRename}
              onChangeIcon={handleChangeIcon}
              onMove={handleMove}
              renamingPageId={renamingPageId}
              onRenamingDone={() => {
                    if (renamingPageId) {
                      router.push(`/docs/${renamingPageId}`);
                    }
                    setRenamingPageId(null);
                  }}
              loading={isTreeLoading}
            />
          </SidebarGroupContent>
        </SidebarGroup>
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

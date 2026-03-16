"use client";

import React, { useContext, useEffect, useMemo } from "react";
import { usePathname } from "next/navigation";
import { useTheme } from "next-themes";
import { Plus, NotebookPen, Sun, Moon, LogOut, Loader2, RefreshCw, Building2, GripVertical } from "lucide-react";
import {
  DndContext,
  closestCenter,
} from "@dnd-kit/core";
import {
  SortableContext,
  verticalListSortingStrategy,
  useSortable,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
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
import { useCollapsedSections } from "@/hooks/use-collapsed-sections";
import { useWorkspaceManagement } from "@/hooks/use-workspace-management";
import { usePageOperations } from "@/hooks/use-page-operations";
import type { Page, Workspace } from "@/types";
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

function SortableWorkspaceItem({ workspace, children }: { workspace: Workspace; children: React.ReactNode }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: workspace.id,
  });
  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : undefined,
  };
  return (
    <div ref={setNodeRef} style={style} {...attributes}>
      {/* Inject listeners via context so the grip handle can use them */}
      <WorkspaceDragContext.Provider value={listeners ?? {}}>
        {children}
      </WorkspaceDragContext.Provider>
    </div>
  );
}

// Context to pass drag listeners down to the grip handle
const WorkspaceDragContext = React.createContext<Record<string, unknown>>({});

function WorkspaceDragHandle() {
  const listeners = useContext(WorkspaceDragContext);
  return (
    <button
      type="button"
      className="mr-0.5 inline-flex shrink-0 cursor-grab items-center justify-center rounded-sm p-0.5 opacity-0 group-hover/ws:opacity-100 hover:bg-sidebar-accent transition-all active:cursor-grabbing"
      aria-label="Drag to reorder"
      {...listeners}
    >
      <GripVertical className="size-3 text-muted-foreground" />
    </button>
  );
}

export function AppSidebar() {
  const { resolvedTheme, setTheme } = useTheme();
  const { user, logout } = useAuth();
  const pathname = usePathname();
  const { data: workspaces = [] } = useWorkspaces();
  const { data: allPages = [], isLoading: isTreeLoading } = usePageTree();

  const activePageId = pathname?.startsWith("/docs/")
    ? decodeURIComponent(pathname.split("/")[2] ?? "") || null
    : null;

  // Extracted hooks
  const { collapsedSections, toggleSection, uncollapseSection, hydrated } = useCollapsedSections();

  const {
    isSyncing,
    handleSync,
    workspaceSensors,
    workspaceIds,
    handleWorkspaceReorder,
    emojiPickerWorkspaceId,
    setEmojiPickerWorkspaceId,
    handleWorkspaceIcon,
  } = useWorkspaceManagement(workspaces);

  const {
    renamingPageId,
    deletingPageId,
    setDeletingPageId,
    addingTo,
    addingChildParentId,
    movingPageId,
    deletingPage,
    deleteChildCount,
    handleDelete,
    handleRename,
    handleChangeIcon,
    handleMove,
    handleAddChild,
    handleAddRootPage,
    handleAddPrivatePage,
    handleRenamingDone,
  } = usePageOperations(allPages, activePageId);

  // Uncollapse workspace section when a new root page needs renaming
  useEffect(() => {
    if (!renamingPageId) return;
    // Find the page across all workspace page lists
    for (const ws of workspaces) {
      const wsPages = allPages.filter((p) => p.workspaceId === ws.id);
      if (wsPages.some((p) => p.id === renamingPageId)) {
        uncollapseSection(ws.id);
        return;
      }
    }
    // Check private pages
    if (allPages.filter((p) => p.isPrivate).some((p) => p.id === renamingPageId)) {
      uncollapseSection("private");
    }
  }, [renamingPageId, allPages, workspaces, uncollapseSection]);

  // Derived data
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

  // User display helpers
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
        {/* One collapsible section per workspace — drag to reorder */}
        <DndContext sensors={workspaceSensors} collisionDetection={closestCenter} onDragEnd={handleWorkspaceReorder}>
          <SortableContext items={workspaceIds} strategy={verticalListSortingStrategy}>
            {workspaces.map((workspace) => (
              <SortableWorkspaceItem key={workspace.id} workspace={workspace}>
                <Collapsible
                  open={!hydrated || !collapsedSections.has(workspace.id)}
                  onOpenChange={() => toggleSection(workspace.id)}
                >
                  <SidebarGroup>
                    <SidebarGroupLabel asChild className="text-sm font-semibold text-sidebar-foreground">
                      <div className="group/ws flex items-center">
                        <WorkspaceDragHandle />
                        <CollapsibleTrigger asChild>
                          <button
                            type="button"
                            className="mr-1 inline-flex shrink-0 items-center justify-center rounded-sm p-0.5 hover:bg-sidebar-accent transition-colors"
                            aria-label={`Toggle ${workspace.name}`}
                          >
                            <ChevronRight className={`size-3 transition-transform ${!hydrated || !collapsedSections.has(workspace.id) ? "rotate-90" : ""}`} />
                          </button>
                        </CollapsibleTrigger>
                        <Popover
                          open={emojiPickerWorkspaceId === workspace.id}
                          onOpenChange={(open) => setEmojiPickerWorkspaceId(open ? workspace.id : null)}
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
                                setEmojiPickerWorkspaceId(null);
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
                                    setEmojiPickerWorkspaceId(null);
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
                          movingPageId={movingPageId}
                        />
                      </SidebarGroupContent>
                    </CollapsibleContent>
                  </SidebarGroup>
                </Collapsible>
              </SortableWorkspaceItem>
            ))}
          </SortableContext>
        </DndContext>

        {workspaces.length > 0 && <SidebarSeparator />}

        {/* Private pages */}
        <Collapsible
          open={!hydrated || !collapsedSections.has("private")}
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
                    <ChevronRight className={`size-3 transition-transform ${!hydrated || !collapsedSections.has("private") ? "rotate-90" : ""}`} />
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

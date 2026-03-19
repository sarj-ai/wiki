"use client";

import { useEffect, useMemo } from "react";
import { usePathname } from "next/navigation";
import { RefreshCw } from "lucide-react";
import {
  Sidebar,
  SidebarContent,
  SidebarHeader,
  SidebarRail,
  SidebarSeparator,
} from "@/components/ui/sidebar";
import { useWorkspaces } from "@/hooks/use-workspaces";
import { usePageTree } from "@/hooks/use-page-tree";
import { useCollapsedSections } from "@/hooks/use-collapsed-sections";
import { useWorkspaceManagement } from "@/hooks/use-workspace-management";
import { usePageOperations } from "@/hooks/use-page-operations";
import type { Page } from "@/types";
import { CommandPalette } from "@/components/search/command-palette";
import { WorkspaceSection } from "./workspace-section";
import { PrivateSection } from "./private-section";
import { UserFooter } from "./user-footer";
import { DeletePageDialog } from "./delete-dialog";

export function AppSidebar() {
  const pathname = usePathname();
  const { data: workspaces = [] } = useWorkspaces();
  const { data: allPages = [], isLoading: isTreeLoading } = usePageTree();

  let activePageId: string | null = null;
  if (pathname?.startsWith("/docs/")) {
    try {
      activePageId = decodeURIComponent(pathname.split("/")[2] ?? "") || null;
    } catch {
      activePageId = pathname.split("/")[2] ?? null;
    }
  }

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
    handleForceSync,
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
    for (const ws of workspaces) {
      const wsPages = allPages.filter((p) => p.workspaceId === ws.id);
      if (wsPages.some((p) => p.id === renamingPageId)) {
        uncollapseSection(ws.id);
        return;
      }
    }
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

  return (
    <Sidebar>
      <SidebarHeader>
        <div className="flex items-center justify-between px-2 py-1.5">
          <span className="text-sm font-semibold">Sarj Docs</span>
          <button
            type="button"
            onClick={() => handleSync()}
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
        <WorkspaceSection
          workspaces={workspaces}
          pagesByWorkspace={pagesByWorkspace}
          collapsedSections={collapsedSections}
          hydrated={hydrated}
          toggleSection={toggleSection}
          activePageId={activePageId}
          workspaceSensors={workspaceSensors}
          workspaceIds={workspaceIds}
          handleWorkspaceReorder={handleWorkspaceReorder}
          emojiPickerWorkspaceId={emojiPickerWorkspaceId}
          setEmojiPickerWorkspaceId={setEmojiPickerWorkspaceId}
          handleWorkspaceIcon={handleWorkspaceIcon}
          handleAddRootPage={handleAddRootPage}
          handleAddChild={handleAddChild}
          setDeletingPageId={setDeletingPageId}
          handleRename={handleRename}
          handleChangeIcon={handleChangeIcon}
          handleMove={handleMove}
          renamingPageId={renamingPageId}
          handleRenamingDone={handleRenamingDone}
          isTreeLoading={isTreeLoading}
          addingTo={addingTo}
          addingChildParentId={addingChildParentId}
          movingPageId={movingPageId}
        />

        {workspaces.length > 0 && <SidebarSeparator />}

        <PrivateSection
          privatePages={privatePages}
          collapsedSections={collapsedSections}
          hydrated={hydrated}
          toggleSection={toggleSection}
          activePageId={activePageId}
          handleAddPrivatePage={handleAddPrivatePage}
          handleAddChild={handleAddChild}
          setDeletingPageId={setDeletingPageId}
          handleRename={handleRename}
          handleChangeIcon={handleChangeIcon}
          handleMove={handleMove}
          renamingPageId={renamingPageId}
          handleRenamingDone={handleRenamingDone}
          isTreeLoading={isTreeLoading}
          addingTo={addingTo}
          addingChildParentId={addingChildParentId}
        />
      </SidebarContent>

      <UserFooter onForceSync={handleForceSync} />
      <SidebarRail />

      <DeletePageDialog
        deletingPageId={deletingPageId}
        deletingPage={deletingPage}
        deleteChildCount={deleteChildCount}
        onClose={() => setDeletingPageId(null)}
        onConfirm={handleDelete}
      />
    </Sidebar>
  );
}

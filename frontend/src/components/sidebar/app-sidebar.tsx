"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { usePathname } from "next/navigation";
import { ChevronsDownUp, ChevronsUpDown, RefreshCw } from "lucide-react";
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
import { useStarredPages } from "@/hooks/use-starred-pages";
import type { Page } from "@/types";
import { CommandPalette } from "@/components/search/command-palette";
import { WorkspaceSection } from "./workspace-section";
import { StarredSection } from "./starred-section";
import { SharedWithMeSection } from "./shared-with-me-section";
import { PrivateSection } from "./private-section";
import { UserFooter } from "./user-footer";
import { DeletePageDialog } from "./delete-dialog";
import { ImportDialog } from "@/components/import/import-dialog";

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
  const { collapsedSections, toggleSection, uncollapseSection, collapseAll, expandAll, hydrated } = useCollapsedSections();

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

  const { starredIds, toggleStar } = useStarredPages();

  // Import dialog state
  const [importTarget, setImportTarget] = useState<{ parentId?: string; workspaceId?: string } | null>(null);

  const findPageInTree = useCallback((pages: Page[], id: string): Page | null => {
    for (const page of pages) {
      if (page.id === id) return page;
      if (page.children.length > 0) {
        const found = findPageInTree(page.children, id);
        if (found) return found;
      }
    }
    return null;
  }, []);

  const handleImport = useCallback((pageId: string) => {
    const page = findPageInTree(allPages, pageId);
    setImportTarget({ parentId: pageId, workspaceId: page?.workspaceId ?? undefined });
  }, [allPages, findPageInTree]);

  const handleImportToWorkspace = useCallback((workspaceId: string) => {
    setImportTarget({ workspaceId });
  }, []);

  const handleImportToDrafts = useCallback(() => {
    setImportTarget({});
  }, []);

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

  // Pages that belong to workspaces the user is NOT a member of
  // (shared via explicit permission)
  const sharedWithMePages = useMemo(() => {
    const wsIds = new Set(workspaces.map((w) => w.id));
    return allPages.filter(
      (p) => !p.isPrivate && p.workspaceId && !wsIds.has(p.workspaceId)
    );
  }, [allPages, workspaces]);

  const allSectionIds = useMemo(
    () => ["starred", ...workspaces.map((w) => w.id), "shared", "private"],
    [workspaces],
  );
  const allCollapsed = hydrated && allSectionIds.length > 0 && allSectionIds.every((id) => collapsedSections.has(id));

  const handleToggleCollapseAll = useCallback(() => {
    if (allCollapsed) {
      expandAll();
    } else {
      collapseAll(allSectionIds);
    }
  }, [allCollapsed, allSectionIds, collapseAll, expandAll]);

  return (
    <Sidebar>
      <SidebarHeader>
        <div className="flex items-center justify-between px-2 py-1.5">
          <span className="text-sm font-semibold">Sarj Docs</span>
          <div className="flex items-center gap-0.5">
            <button
              type="button"
              onClick={handleToggleCollapseAll}
              title={allCollapsed ? "Expand all sections" : "Collapse all sections"}
              className="flex size-6 items-center justify-center rounded-md text-sidebar-foreground ring-sidebar-ring outline-hidden hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:ring-2 transition-colors"
            >
              {allCollapsed ? (
                <ChevronsUpDown className="size-3.5" />
              ) : (
                <ChevronsDownUp className="size-3.5" />
              )}
            </button>
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
        </div>
        <div className="px-2">
          <CommandPalette />
        </div>
      </SidebarHeader>

      <SidebarContent>
        {starredIds.size > 0 && (
          <>
            <StarredSection
              starredIds={starredIds}
              allPages={allPages}
              activePageId={activePageId}
              collapsed={hydrated && collapsedSections.has("starred")}
              onToggle={() => toggleSection("starred")}
            />
            <SidebarSeparator />
          </>
        )}
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
          starredIds={starredIds}
          onToggleStar={toggleStar}
          onImport={handleImport}
          onImportToWorkspace={handleImportToWorkspace}
        />

        {workspaces.length > 0 && <SidebarSeparator />}

        {sharedWithMePages.length > 0 && (
          <>
            <SharedWithMeSection
              pages={sharedWithMePages}
              activePageId={activePageId}
              collapsed={hydrated && collapsedSections.has("shared")}
              onToggle={() => toggleSection("shared")}
            />
            <SidebarSeparator />
          </>
        )}

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
          starredIds={starredIds}
          onToggleStar={toggleStar}
          onImport={handleImport}
          onImportToDrafts={handleImportToDrafts}
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

      <ImportDialog
        open={importTarget !== null}
        onOpenChange={(open) => { if (!open) setImportTarget(null); }}
        parentId={importTarget?.parentId}
        workspaceId={importTarget?.workspaceId}
      />
    </Sidebar>
  );
}

"use client";

import {
  closestCorners,
  type CollisionDetection,
  DndContext,
  DragOverlay,
  pointerWithin,
  rectIntersection,
} from "@dnd-kit/core";
import { usePathname } from "next/navigation";
import { useEffect, useId } from "react";

import { ErrorBoundary } from "@/components/error-boundary";
import { RecentPagesLink } from "@/components/recent-pages/recent-pages-link";
import { SarjLogo } from "@/components/sarj-logo";
import { Button } from "@/components/ui/button";
import { Sidebar, SidebarContent, SidebarHeader, SidebarSeparator } from "@/components/ui/sidebar";
import { consumeRevealFor, expandPath } from "@/hooks/use-expanded-folders";
import {
  useDocIcons,
  useDrives,
  useFile,
  useFolderIcons,
  usePreferences,
  useSortOrders,
} from "@/hooks/use-wiki-tree";
import { useBrowserUserId } from "@/lib/browser-user-context";
import { renderNodeIcon } from "@/lib/file-icons";
import { EMPTY_DOC_ICONS, EMPTY_FOLDER_ICONS, EMPTY_SORT_ORDERS } from "@/lib/wiki-indexes";

import { DriveSearchBox } from "./drive-search-box";
import { DriveSection } from "./drive-section";
import { MoveDialogOutletProvider } from "./move-dialog-outlet";
import { PreferencesDialogOutletProvider } from "./preferences-dialog-outlet";
import { SidebarResizeHandle } from "./sidebar-resize-handle";
import { SidebarNavigationProvider, useSidebarNavigation } from "./use-sidebar-navigation";
import { useWikiReorder } from "./use-wiki-reorder";
import { WikiFooter } from "./wiki-footer";

const cursorFirstCollision: CollisionDetection = (args) => {
  const pointer = pointerWithin(args);
  if (pointer.length > 0) return pointer;
  const intersect = rectIntersection(args);
  if (intersect.length > 0) return intersect;
  return closestCorners(args);
};

export function WikiSidebar() {
  const dragContextId = useId();
  const userId = useBrowserUserId();
  const pathname = usePathname();
  const drivesQuery = useDrives();
  const prefsQuery = usePreferences();
  const folderIconsQuery = useFolderIcons();
  const sortOrdersQuery = useSortOrders();
  const reorder = useWikiReorder();

  const activeDriveId: null | string = (() => {
    if (!pathname.startsWith("/d/")) return null;
    try {
      return decodeURIComponent(pathname.split("/", 3)[2] ?? "") || null;
    } catch {
      return pathname.split("/", 3)[2] ?? null;
    }
  })();

  const showNonDocFiles = prefsQuery.data?.showNonDocFiles ?? true;
  const hiddenWorkspaceIds = new Set(prefsQuery.data?.hiddenWorkspaceIds);
  const visibleDrives = (drivesQuery.data ?? []).filter((d) => !hiddenWorkspaceIds.has(d.id));
  const docIconsQuery = useDocIcons(visibleDrives.length > 0);

  const activeFileQuery = useFile(activeDriveId);
  const breadcrumb = activeFileQuery.data?.breadcrumb;
  useEffect(() => {
    if (
      !activeDriveId ||
      !breadcrumb ||
      breadcrumb.length === 0 ||
      !consumeRevealFor(activeDriveId)
    )
      return;
    const [driveCrumb, ...folderCrumbs] = breadcrumb;
    if (!driveCrumb) return;
    expandPath(
      userId,
      driveCrumb.id,
      folderCrumbs.map((c) => c.id),
    );

    const targetId = activeDriveId;
    let pulseTimer: number | undefined;
    let revealed: HTMLElement | null = null;
    const handle = requestAnimationFrame(() => {
      const el = document.querySelector<HTMLElement>(`[data-file-id="${CSS.escape(targetId)}"]`);
      if (!el) return;
      el.scrollIntoView({ block: "nearest" });
      revealed = el;
      el.classList.add("animate-reveal-pulse");
      pulseTimer = window.setTimeout(() => {
        el.classList.remove("animate-reveal-pulse");
      }, 1200);
    });
    return () => {
      cancelAnimationFrame(handle);
      window.clearTimeout(pulseTimer);
      revealed?.classList.remove("animate-reveal-pulse");
    };
  }, [activeDriveId, breadcrumb, userId]);

  return (
    <SidebarNavigationProvider ownerKey={userId} pathname={pathname}>
      <MoveDialogOutletProvider userId={userId}>
        <PreferencesDialogOutletProvider userId={userId}>
          <Sidebar>
            <SidebarHeader>
              <SidebarHomeLink />
              <div className="px-2 pb-1">
                <DriveSearchBox />
              </div>
            </SidebarHeader>
            <DndContext
              collisionDetection={cursorFirstCollision}
              id={dragContextId}
              onDragCancel={reorder.onDragCancel}
              onDragEnd={reorder.onDragEnd}
              onDragOver={reorder.onDragOver}
              onDragStart={reorder.onDragStart}
              sensors={reorder.sensors}
            >
              <SidebarContent>
                <ErrorBoundary>
                  {drivesQuery.isLoading ? (
                    <div className="space-y-2 px-3 py-2" role="status">
                      <span className="text-muted-foreground text-xs">Loading drives…</span>
                      <div aria-hidden="true" className="space-y-2">
                        <div className="bg-muted h-7 w-full rounded-sm" />
                        <div className="bg-muted h-7 w-4/5 rounded-sm" />
                        <div className="bg-muted h-7 w-full rounded-sm" />
                      </div>
                    </div>
                  ) : null}
                  {drivesQuery.isError ? (
                    <div className="space-y-2 px-3 py-2" role="alert">
                      <p className="text-destructive text-xs">Failed to load drives</p>
                      <Button
                        disabled={drivesQuery.isFetching}
                        onClick={() => {
                          void drivesQuery
                            .refetch()
                            .catch(() => console.warn("Drive retry could not finish"));
                        }}
                        size="sm"
                        variant="outline"
                      >
                        Retry drives
                      </Button>
                    </div>
                  ) : null}
                  {visibleDrives.map((drive, index) => (
                    <div key={drive.id}>
                      <DriveSection
                        activeDriveId={activeDriveId}
                        defaultExpanded={index === 0}
                        docIcons={docIconsQuery.data ?? EMPTY_DOC_ICONS}
                        drive={drive}
                        folderIcons={folderIconsQuery.data ?? EMPTY_FOLDER_ICONS}
                        optimisticByContainer={reorder.optimisticByContainer}
                        pathname={pathname}
                        showNonDocFiles={showNonDocFiles}
                        sortOrders={sortOrdersQuery.data ?? EMPTY_SORT_ORDERS}
                      />
                      {index < visibleDrives.length - 1 && <SidebarSeparator />}
                    </div>
                  ))}
                  {!drivesQuery.isLoading && !drivesQuery.isError && visibleDrives.length === 0 && (
                    <div className="text-muted-foreground px-3 py-2 text-xs">
                      No accessible drives
                    </div>
                  )}
                </ErrorBoundary>
              </SidebarContent>
              <DragOverlay dropAnimation={null}>
                {reorder.activeNode ? (
                  <div className="bg-sidebar text-sidebar-foreground flex max-w-64 items-center gap-2 rounded-md border px-2.5 py-1.5 text-sm shadow-lg">
                    {renderNodeIcon(reorder.activeNode, "size-4 shrink-0")}
                    <span className="truncate">{reorder.activeNode.name}</span>
                  </div>
                ) : null}
              </DragOverlay>
            </DndContext>
            <WikiFooter />
            <SidebarResizeHandle />
          </Sidebar>
        </PreferencesDialogOutletProvider>
      </MoveDialogOutletProvider>
    </SidebarNavigationProvider>
  );
}

function SidebarHomeLink() {
  const navigateSidebar = useSidebarNavigation();
  return (
    <RecentPagesLink
      className="focus-visible:ring-ring flex items-center gap-2 rounded-sm px-2 py-1.5 focus-visible:ring-2 focus-visible:outline-none"
      onNavigate={() => navigateSidebar("/")}
    >
      <SarjLogo size={28} />
    </RecentPagesLink>
  );
}

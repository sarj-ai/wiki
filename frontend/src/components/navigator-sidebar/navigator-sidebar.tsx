"use client";

import { usePathname } from "next/navigation";
import { useEffect, useMemo } from "react";

import { ErrorBoundary } from "@/components/error-boundary";
import { SarjLogo } from "@/components/sarj-logo";
import { Sidebar, SidebarContent, SidebarHeader, SidebarSeparator } from "@/components/ui/sidebar";
import { expandPath } from "@/hooks/use-expanded-folders";
import {
  useDrives,
  useFile,
  useFolderIcons,
  usePreferences,
  useSortOrders,
} from "@/hooks/use-navigator-tree";

import { DriveSearchBox } from "./drive-search-box";
import { DriveSection } from "./drive-section";
import { NavigatorFooter } from "./navigator-footer";
import { SidebarResizeHandle } from "./resize-handle";

export function NavigatorSidebar() {
  const pathname = usePathname();
  const drivesQuery = useDrives();
  const prefsQuery = usePreferences();
  const folderIconsQuery = useFolderIcons();
  const sortOrdersQuery = useSortOrders();

  const activeDriveId: string | null = (() => {
    if (!pathname.startsWith("/d/")) return null;
    try {
      return decodeURIComponent(pathname.split("/")[2] ?? "") || null;
    } catch {
      return pathname.split("/")[2] ?? null;
    }
  })();

  const showNonDocFiles = prefsQuery.data?.showNonDocFiles ?? true;
  const hiddenWorkspaceIds = useMemo(
    () => new Set(prefsQuery.data?.hiddenWorkspaceIds ?? []),
    [prefsQuery.data],
  );

  const visibleDrives = useMemo(
    () => (drivesQuery.data ?? []).filter((d) => !hiddenWorkspaceIds.has(d.id)),
    [drivesQuery.data, hiddenWorkspaceIds],
  );

  // Reveal the active file in the sidebar tree. When the user opens
  // a file via the command palette (or any deep link), its parent
  // folders are usually still collapsed — so the row that should
  // light up never even gets rendered. The breadcrumb endpoint is
  // already fetched for the toolbar; reuse it here to expand every
  // ancestor on the chain. The breadcrumb is [drive, ...folders],
  // which is exactly what `expandPath` consumes.
  const activeFileQuery = useFile(activeDriveId);
  const breadcrumb = activeFileQuery.data?.breadcrumb;
  useEffect(() => {
    if (!breadcrumb || breadcrumb.length === 0) return;
    const [driveCrumb, ...folderCrumbs] = breadcrumb;
    if (!driveCrumb) return;
    expandPath(
      driveCrumb.id,
      folderCrumbs.map((c) => c.id),
    );
  }, [breadcrumb]);

  return (
    <Sidebar>
      <SidebarHeader>
        <div className="flex items-center gap-2 px-2 py-1.5">
          <SarjLogo size={28} priority />
        </div>
        <div className="px-2 pb-1">
          <DriveSearchBox />
        </div>
      </SidebarHeader>
      <SidebarContent>
        <ErrorBoundary>
          {drivesQuery.isLoading && (
            <div className="text-muted-foreground px-3 py-2 text-xs">Loading drives…</div>
          )}
          {drivesQuery.isError && (
            <div className="text-destructive px-3 py-2 text-xs">Failed to load drives</div>
          )}
          {visibleDrives.map((drive, index) => (
            <div key={drive.id}>
              <DriveSection
                drive={drive}
                activeDriveId={activeDriveId}
                showNonDocFiles={showNonDocFiles}
                folderIcons={folderIconsQuery.data ?? []}
                sortOrders={sortOrdersQuery.data ?? []}
              />
              {index < visibleDrives.length - 1 && <SidebarSeparator />}
            </div>
          ))}
          {!drivesQuery.isLoading && visibleDrives.length === 0 && (
            <div className="text-muted-foreground px-3 py-2 text-xs">No accessible drives</div>
          )}
        </ErrorBoundary>
      </SidebarContent>
      <NavigatorFooter />
      <SidebarResizeHandle />
    </Sidebar>
  );
}

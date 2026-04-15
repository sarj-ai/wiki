"use client";

import { useMemo } from "react";
import { usePathname } from "next/navigation";
import {
  Sidebar,
  SidebarContent,
  SidebarHeader,
  SidebarRail,
  SidebarSeparator,
} from "@/components/ui/sidebar";
import {
  useDrives,
  useFolderIcons,
  usePreferences,
  useSortOrders,
} from "@/hooks/use-navigator-tree";
import { ErrorBoundary } from "@/components/error-boundary";
import { DriveSection } from "./drive-section";

export function NavigatorSidebar() {
  const pathname = usePathname();
  const drivesQuery = useDrives();
  const prefsQuery = usePreferences();
  const folderIconsQuery = useFolderIcons();
  const sortOrdersQuery = useSortOrders();

  const activeDriveId: string | null = useMemo(() => {
    if (!pathname?.startsWith("/d/")) return null;
    try {
      return decodeURIComponent(pathname.split("/")[2] ?? "") || null;
    } catch {
      return pathname.split("/")[2] ?? null;
    }
  }, [pathname]);

  const showNonDocFiles = prefsQuery.data?.showNonDocFiles ?? true;
  const hiddenWorkspaceIds = useMemo(
    () => new Set(prefsQuery.data?.hiddenWorkspaceIds ?? []),
    [prefsQuery.data],
  );

  const visibleDrives = useMemo(
    () => (drivesQuery.data ?? []).filter((d) => !hiddenWorkspaceIds.has(d.id)),
    [drivesQuery.data, hiddenWorkspaceIds],
  );

  return (
    <Sidebar>
      <SidebarHeader>
        <div className="flex items-center gap-2 px-2 py-1.5">
          <span className="text-sm font-semibold">Navigator</span>
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
      <SidebarRail />
    </Sidebar>
  );
}

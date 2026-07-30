"use client";

import { usePathname } from "next/navigation";
import { useEffect } from "react";

import { ErrorBoundary } from "@/components/error-boundary";
import { SarjLogo } from "@/components/sarj-logo";
import { Sidebar, SidebarContent, SidebarHeader, SidebarSeparator } from "@/components/ui/sidebar";
import { consumeRevealFor, expandPath } from "@/hooks/use-expanded-folders";
import {
  useDrives,
  useFile,
  useFolderIcons,
  usePreferences,
  useSortOrders,
} from "@/hooks/use-wiki-tree";

import { DriveSearchBox } from "./drive-search-box";
import { DriveSection } from "./drive-section";
import { SidebarResizeHandle } from "./sidebar-resize-handle";
import { WikiFooter } from "./wiki-footer";

export function WikiSidebar() {
  const pathname = usePathname();
  const drivesQuery = useDrives();
  const prefsQuery = usePreferences();
  const folderIconsQuery = useFolderIcons();
  const sortOrdersQuery = useSortOrders();

  const activeDriveId: null | string = (() => {
    if (!pathname.startsWith("/d/")) return null;
    try {
      return decodeURIComponent(pathname.split("/")[2] ?? "") || null;
    } catch {
      return pathname.split("/")[2] ?? null;
    }
  })();

  const showNonDocFiles = prefsQuery.data?.showNonDocFiles ?? true;
  const hiddenWorkspaceIds = new Set(prefsQuery.data?.hiddenWorkspaceIds ?? []);
  const visibleDrives = (drivesQuery.data ?? []).filter((d) => !hiddenWorkspaceIds.has(d.id));

  const activeFileQuery = useFile(activeDriveId);
  const breadcrumb = activeFileQuery.data?.breadcrumb;
  useEffect(() => {
    if (!activeDriveId || !breadcrumb || breadcrumb.length === 0) return;
    if (!consumeRevealFor(activeDriveId)) return;
    const [driveCrumb, ...folderCrumbs] = breadcrumb;
    if (!driveCrumb) return;
    expandPath(
      driveCrumb.id,
      folderCrumbs.map((c) => c.id),
    );

    const targetId = activeDriveId;
    const handle = requestAnimationFrame(() => {
      const el = document.querySelector<HTMLElement>(`[data-file-id="${CSS.escape(targetId)}"]`);
      if (!el) return;
      el.scrollIntoView({ block: "nearest" });
      el.classList.add("animate-reveal-pulse");
      window.setTimeout(() => {
        el.classList.remove("animate-reveal-pulse");
      }, 1200);
    });
    return () => {
      cancelAnimationFrame(handle);
    };
  }, [activeDriveId, breadcrumb]);

  return (
    <Sidebar>
      <SidebarHeader>
        <div className="flex items-center gap-2 px-2 py-1.5">
          <SarjLogo priority size={28} />
        </div>
        <div className="px-2 pb-1">
          <DriveSearchBox />
        </div>
      </SidebarHeader>
      <SidebarContent>
        <ErrorBoundary>
          {drivesQuery.isLoading ? (
            <div className="text-muted-foreground px-3 py-2 text-xs">Loading drives…</div>
          ) : null}
          {drivesQuery.isError ? (
            <div className="text-destructive px-3 py-2 text-xs">Failed to load drives</div>
          ) : null}
          {visibleDrives.map((drive, index) => (
            <div key={drive.id}>
              <DriveSection
                activeDriveId={activeDriveId}
                drive={drive}
                folderIcons={folderIconsQuery.data ?? []}
                showNonDocFiles={showNonDocFiles}
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
      <WikiFooter />
      <SidebarResizeHandle />
    </Sidebar>
  );
}

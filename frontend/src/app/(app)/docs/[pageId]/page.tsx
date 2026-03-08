"use client";

import { use, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { FileQuestion, Maximize2, Minimize2, PanelLeft, PanelLeftClose } from "lucide-react";
import { useSidebar } from "@/components/ui/sidebar";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useLoading } from "@/components/loading-bar";
import { usePageTree } from "@/hooks/use-page-tree";
import { useWorkspaces } from "@/hooks/use-workspaces";
import { getPageBreadcrumbs } from "@/lib/page-utils";
import { DocumentHeader } from "@/components/document/document-header";
import { DocumentIframe } from "@/components/document/document-iframe";
import { DocumentSkeleton } from "@/components/document/document-skeleton";

export default function DocumentPage({
  params,
}: {
  params: Promise<{ pageId: string }>;
}) {
  const { pageId } = use(params);
  const router = useRouter();
  const { open: sidebarOpen, setOpen: setSidebarOpen, toggleSidebar } = useSidebar();
  const [focusMode, setFocusMode] = useState(false);
  const sidebarWasOpen = useRef(true);

  function enterFocusMode() {
    sidebarWasOpen.current = sidebarOpen;
    if (sidebarOpen) setSidebarOpen(false);
    setFocusMode(true);
  }

  function exitFocusMode() {
    if (sidebarWasOpen.current) setSidebarOpen(true);
    setFocusMode(false);
  }

  function toggleFocusMode() {
    if (focusMode) exitFocusMode();
    else enterFocusMode();
  }

  // Cmd+. / Ctrl+. to toggle focus mode
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key === "." && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        toggleFocusMode();
      }
    }
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  });
  const { data: page, isLoading, error } = useQuery({
    queryKey: ["pages", pageId],
    queryFn: () => api.getPage(pageId),
    retry: false, // don't retry on 403/404 — page is gone
  });
  const { data: allPages = [] } = usePageTree();
  const { data: workspaces = [] } = useWorkspaces();
  const queryClient = useQueryClient();
  const { start, done } = useLoading();

  async function handleChangeIcon(icon: string | null) {
    start();
    try {
      await api.updatePage(pageId, { icon });
      queryClient.invalidateQueries({ queryKey: ["pages"] });
    } finally {
      done();
    }
  }

  async function handleRename(newTitle: string) {
    start();
    try {
      await api.updatePage(pageId, { title: newTitle });
      queryClient.invalidateQueries({ queryKey: ["pages"] });
      queryClient.invalidateQueries({ queryKey: ["pages", pageId] });
    } finally {
      done();
    }
  }

  // Redirect to home when page is deleted/not found
  useEffect(() => {
    if (!isLoading && (error || !page)) {
      router.replace("/");
    }
  }, [isLoading, error, page, router]);

  if (isLoading) {
    return <DocumentSkeleton />;
  }

  if (error || !page) {
    return (
      <div className="flex h-full min-h-svh flex-col items-center justify-center gap-4">
        <div className="flex size-16 items-center justify-center rounded-2xl bg-muted">
          <FileQuestion className="size-8 text-muted-foreground" />
        </div>
        <div className="text-center">
          <h2 className="text-lg font-medium text-foreground">
            Page not found
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            The document you are looking for does not exist or you do not have
            access.
          </p>
        </div>
      </div>
    );
  }

  const workspace = page.workspaceId
    ? workspaces.find((w) => w.id === page.workspaceId) ?? null
    : null;

  const breadcrumbs = getPageBreadcrumbs(page.id, allPages);

  return (
    <div className="flex h-full min-h-svh flex-col">
      {focusMode ? (
        <div className="flex items-center gap-2 px-3 py-1.5 border-b">
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                onClick={toggleSidebar}
                className="flex size-7 items-center justify-center rounded-md text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
              >
                {sidebarOpen ? (
                  <PanelLeftClose className="size-4" />
                ) : (
                  <PanelLeft className="size-4" />
                )}
              </button>
            </TooltipTrigger>
            <TooltipContent side="bottom">
              {sidebarOpen ? "Hide sidebar" : "Show sidebar"}
            </TooltipContent>
          </Tooltip>
          <span className="text-sm font-medium truncate flex-1">
            {page.icon && <span className="mr-1.5">{page.icon}</span>}
            {page.title}
          </span>
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                onClick={exitFocusMode}
                className="flex size-7 items-center justify-center rounded-md text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
              >
                <Minimize2 className="size-4" />
              </button>
            </TooltipTrigger>
            <TooltipContent side="bottom">Exit focus mode (⌘.)</TooltipContent>
          </Tooltip>
        </div>
      ) : (
        <div className="flex flex-col gap-4 px-6 pt-6 pb-4">
          <DocumentHeader
            page={page}
            breadcrumbs={breadcrumbs}
            workspace={workspace}
            onChangeIcon={handleChangeIcon}
            onRename={handleRename}
            onFocusMode={enterFocusMode}
          />
        </div>
      )}

      <div className={`flex flex-1 min-h-0 ${focusMode ? "px-0 pb-0" : "px-6 pb-6"}`}>
        <DocumentIframe googleDriveId={page.googleDriveId} />
      </div>
    </div>
  );
}

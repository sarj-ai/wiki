"use client";

import { use, useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  FileQuestion,
  Minimize2,
  PanelLeft,
  PanelLeftClose,
  Pencil,
  Eye,
  Settings2,
  RefreshCw,
} from "lucide-react";
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
import { DocumentToolbar, DocumentTitle } from "@/components/document/document-header";
import { DocumentIframe } from "@/components/document/document-iframe";
import { DocumentSkeleton } from "@/components/document/document-skeleton";
import { DocViewer } from "@/components/document/doc-viewer";
import { ContentLoadingSkeleton } from "@/components/document/content-loading-skeleton";

const EDITOR_PREF_KEY = "sarj-always-iframe";

function useEditorPreference() {
  const [alwaysIframe, setAlwaysIframe] = useState(false);

  useEffect(() => {
    setAlwaysIframe(localStorage.getItem(EDITOR_PREF_KEY) === "true");
  }, []);

  const toggle = useCallback(() => {
    setAlwaysIframe((prev) => {
      const next = !prev;
      localStorage.setItem(EDITOR_PREF_KEY, String(next));
      return next;
    });
  }, []);

  return { alwaysIframe, toggleAlwaysIframe: toggle };
}

const TOOLBAR_BUTTON_CLASS =
  "flex size-7 items-center justify-center rounded-md transition-colors";
const TOOLBAR_BUTTON_IDLE =
  "text-muted-foreground hover:text-white hover:bg-[#251040] dark:hover:bg-[#4B3483]";
const TOOLBAR_BUTTON_ACTIVE =
  "bg-sidebar-accent text-sidebar-accent-foreground";

function EditToggleButton({
  isEditing,
  onToggle,
}: {
  isEditing: boolean;
  onToggle: () => void;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          data-edit-toggle
          onClick={onToggle}
          className={`${TOOLBAR_BUTTON_CLASS} ${isEditing ? TOOLBAR_BUTTON_ACTIVE : TOOLBAR_BUTTON_IDLE}`}
        >
          {isEditing ? <Eye className="size-4" /> : <Pencil className="size-4" />}
        </button>
      </TooltipTrigger>
      <TooltipContent side="bottom">
        {isEditing ? "View mode" : "Edit mode"}
      </TooltipContent>
    </Tooltip>
  );
}

export default function DocumentPage({
  params,
}: {
  params: Promise<{ pageId: string }>;
}) {
  const { pageId } = use(params);
  const router = useRouter();
  const {
    open: sidebarOpen,
    setOpen: setSidebarOpen,
    toggleSidebar,
  } = useSidebar();
  const [focusMode, setFocusMode] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const sidebarWasOpen = useRef(true);
  const { alwaysIframe, toggleAlwaysIframe } = useEditorPreference();

  const saveSidebarState = useCallback(() => {
    sidebarWasOpen.current = sidebarOpen;
    if (sidebarOpen) setSidebarOpen(false);
  }, [sidebarOpen, setSidebarOpen]);

  const restoreSidebarState = useCallback(() => {
    if (sidebarWasOpen.current) setSidebarOpen(true);
  }, [setSidebarOpen]);

  const enterFocusMode = useCallback(() => {
    saveSidebarState();
    setFocusMode(true);
  }, [saveSidebarState]);

  const exitFocusMode = useCallback(() => {
    restoreSidebarState();
    setFocusMode(false);
  }, [restoreSidebarState]);

  const toggleFocusMode = useCallback(() => {
    if (focusMode) {
      restoreSidebarState();
    } else {
      saveSidebarState();
    }
    setFocusMode((prev) => !prev);
  }, [focusMode, saveSidebarState, restoreSidebarState]);

  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key === "." && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        toggleFocusMode();
      }
    }
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [toggleFocusMode]);

  const {
    data: page,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["pages", pageId],
    queryFn: () => api.getPage(pageId),
    retry: false,
  });

  const forceRefreshRef = useRef(false);
  const {
    data: content,
    isLoading: isContentLoading,
    isFetching: isContentFetching,
    refetch: refetchContent,
  } = useQuery({
    queryKey: ["page-content", pageId],
    queryFn: () => {
      const force = forceRefreshRef.current;
      forceRefreshRef.current = false;
      return api.getPageContent(pageId, force);
    },
    enabled: !!page && !alwaysIframe,
    retry: false,
  });

  const { data: allPages = [] } = usePageTree();
  const { data: workspaces = [] } = useWorkspaces();
  const queryClient = useQueryClient();
  const { start, done } = useLoading();

  // When switching from edit back to view, force-refresh from Docs API
  const wasEditing = useRef(false);
  useEffect(() => {
    if (wasEditing.current && !isEditing && !alwaysIframe) {
      forceRefreshRef.current = true;
      queryClient.removeQueries({ queryKey: ["page-content", pageId] });
      // Give React Query a tick to process the removal, then refetch
      setTimeout(() => refetchContent(), 50);
    }
    wasEditing.current = isEditing;
  }, [isEditing, alwaysIframe, pageId, queryClient, refetchContent]);

  const isContentReady = !isContentLoading && !isContentFetching;

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
    ? (workspaces.find((w) => w.id === page.workspaceId) ?? null)
    : null;

  const breadcrumbs = getPageBreadcrumbs(page.id, allPages);
  const showIframe = alwaysIframe || isEditing;

  return (
    <div className="flex flex-col h-svh">
      {focusMode ? (
        <div className="shrink-0 flex items-center gap-2 px-3 py-1.5 border-b bg-background">
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                onClick={toggleSidebar}
                className={`${TOOLBAR_BUTTON_CLASS} ${TOOLBAR_BUTTON_IDLE}`}
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
          <div className="flex items-center gap-1">
            {!alwaysIframe && (
              <EditToggleButton
                isEditing={isEditing}
                onToggle={() => setIsEditing(!isEditing)}
              />
            )}
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  onClick={exitFocusMode}
                  className={`${TOOLBAR_BUTTON_CLASS} ${TOOLBAR_BUTTON_IDLE}`}
                >
                  <Minimize2 className="size-4" />
                </button>
              </TooltipTrigger>
              <TooltipContent side="bottom">
                Exit focus mode (⌘.)
              </TooltipContent>
            </Tooltip>
          </div>
        </div>
      ) : (
        <DocumentToolbar
          page={page}
          breadcrumbs={breadcrumbs}
          workspace={workspace}
          onFocusMode={enterFocusMode}
        >
          <div className="flex items-center gap-1">
            {!alwaysIframe && (
              <EditToggleButton
                isEditing={isEditing}
                onToggle={() => setIsEditing(!isEditing)}
              />
            )}
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  onClick={toggleAlwaysIframe}
                  className={`${TOOLBAR_BUTTON_CLASS} ${alwaysIframe ? TOOLBAR_BUTTON_ACTIVE : TOOLBAR_BUTTON_IDLE}`}
                >
                  <Settings2 className="size-4" />
                </button>
              </TooltipTrigger>
              <TooltipContent side="bottom">
                {alwaysIframe
                  ? "Using Google Docs editor (click to use native viewer)"
                  : "Using native viewer (click to always use Google Docs editor)"}
              </TooltipContent>
            </Tooltip>
          </div>
        </DocumentToolbar>
      )}

      <div
        className={`flex-1 overflow-y-auto ${showIframe ? "flex flex-col" : ""}`}
      >
        {!focusMode && (
          <DocumentTitle
            page={page}
            workspace={workspace}
            onChangeIcon={handleChangeIcon}
            onRename={handleRename}
          />
        )}
        {showIframe ? (
          <div className={`flex flex-1 animate-in fade-in duration-200 ${focusMode ? "" : "px-6 pb-6"}`}>
            <DocumentIframe googleDriveId={page.googleDriveId} />
          </div>
        ) : !isContentReady ? (
          <ContentLoadingSkeleton />
        ) : content?.blocks ? (
          <div className="px-6 pb-6 animate-in fade-in duration-200">
            <div className="mx-auto max-w-3xl py-4">
              <DocViewer blocks={content.blocks} />
            </div>
          </div>
        ) : (
          <div className="flex-1 flex items-center justify-center">
            <div className="flex flex-col items-center gap-3">
              <p className="text-sm text-muted-foreground">
                Unable to load document content.
              </p>
              <button
                type="button"
                onClick={() => refetchContent()}
                className="flex items-center gap-2 text-sm text-primary hover:underline"
              >
                <RefreshCw className="size-4" />
                Retry
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

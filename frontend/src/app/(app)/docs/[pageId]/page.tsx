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
  FileText,
  Search,
  Loader2,
  AlertCircle,
} from "lucide-react";
import { toast } from "sonner";
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
import type { ContentBlock } from "@/types";

function isDocEmpty(blocks: ContentBlock[] | undefined): boolean {
  if (!blocks || blocks.length === 0) return true;
  return blocks.every(
    (b) =>
      b.type === "paragraph" &&
      (!b.runs || b.runs.length === 0 || b.runs.every((r) => !r.text?.trim())),
  );
}

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
  highlight,
}: {
  isEditing: boolean;
  onToggle: () => void;
  highlight?: boolean;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          data-edit-toggle
          onClick={onToggle}
          className={`${TOOLBAR_BUTTON_CLASS} ${isEditing ? TOOLBAR_BUTTON_ACTIVE : TOOLBAR_BUTTON_IDLE} ${highlight ? "edit-toggle-highlight" : ""}`}
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
    refetchInterval: (query) => {
      const p = query.state.data;
      return p?.googleDriveId?.startsWith("pending-") ? 1000 : false;
    },
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
    enabled: !!page,
    retry: false,
  });

  const { data: allPages = [] } = usePageTree();
  const { data: workspaces = [] } = useWorkspaces();
  const queryClient = useQueryClient();
  const { start, done } = useLoading();

  // When switching from edit back to view, force-refresh from Docs API
  const wasEditing = useRef(false);
  useEffect(() => {
    if (wasEditing.current && !isEditing) {
      forceRefreshRef.current = true;
      queryClient.removeQueries({ queryKey: ["page-content", pageId] });
      // Give React Query a tick to process the removal, then refetch
      setTimeout(() => refetchContent(), 50);
    }
    wasEditing.current = isEditing;
  }, [isEditing, alwaysIframe, pageId, queryClient, refetchContent]);

  const isPending = page?.googleDriveId?.startsWith("pending-") ?? false;
  const [pendingElapsed, setPendingElapsed] = useState(0);

  useEffect(() => {
    if (!isPending) {
      setPendingElapsed(0);
      return;
    }
    setPendingElapsed(0);
    const interval = setInterval(() => setPendingElapsed((s) => s + 1), 1000);
    return () => clearInterval(interval);
  }, [isPending]);
  const isContentReady = !isContentLoading && !isContentFetching;
  const docEmpty = isContentReady && !isEditing && isDocEmpty(content?.blocks);
  const showIframe = !isPending && (alwaysIframe || isEditing);

  const [retrying, setRetrying] = useState(false);

  async function handleRetry() {
    setRetrying(true);
    setPendingElapsed(0);
    try {
      await api.retryPage(pageId);
      // Retry is now synchronous — the page has a real Drive ID
      queryClient.invalidateQueries({ queryKey: ["pages", pageId] });
      queryClient.invalidateQueries({ queryKey: ["page-content", pageId] });
    } catch {
      // Failed — polling will show stale state again after 30s
    } finally {
      setRetrying(false);
    }
  }

  async function handleDeleteStale() {
    try {
      await api.deletePage(pageId);
      queryClient.invalidateQueries({ queryKey: ["pages"] });
      router.replace("/");
    } catch {
      toast.error("Failed to delete page");
    }
  }

  function renderContent() {
    if (isPending) {
      const isStale = pendingElapsed > 30 && !retrying;
      return (
        <div className="flex-1 flex items-center justify-center animate-in fade-in duration-300">
          <div className="flex flex-col items-center gap-6 max-w-sm text-center">
            <div className="relative">
              <div className="size-16 rounded-2xl bg-muted flex items-center justify-center">
                {isStale ? (
                  <AlertCircle className="size-8 text-destructive" />
                ) : (
                  <Loader2 className="size-8 text-muted-foreground animate-spin" />
                )}
              </div>
              {!isStale && (
                <div className="absolute -bottom-1 -right-1 size-5 rounded-full bg-background border-2 border-muted flex items-center justify-center">
                  <svg className="size-3 text-muted-foreground" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z" />
                    <polyline points="14 2 14 8 20 8" />
                  </svg>
                </div>
              )}
            </div>
            <div>
              <h3 className="text-base font-medium text-foreground">
                {isStale ? "Something went wrong" : "Setting up your document"}
              </h3>
              <p className="mt-1.5 text-sm text-muted-foreground leading-relaxed">
                {isStale
                  ? "The document couldn't be created in Google Drive. You can retry or delete this page and start over."
                  : "Creating a new Google Doc and syncing with Drive. This usually takes a few seconds."}
              </p>
            </div>
            {isStale ? (
              <div className="flex items-center gap-3">
                <button
                  type="button"
                  onClick={handleRetry}
                  className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
                >
                  <RefreshCw className="size-4" />
                  Retry
                </button>
                <button
                  type="button"
                  onClick={handleDeleteStale}
                  className="inline-flex items-center gap-2 rounded-md border border-border px-4 py-2 text-sm font-medium text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
                >
                  Delete page
                </button>
              </div>
            ) : (
              <div className="flex items-center gap-2 text-xs text-muted-foreground/60">
                <div className="size-1.5 rounded-full bg-green-500 animate-pulse" />
                Syncing with Google Drive
              </div>
            )}
          </div>
        </div>
      );
    }

    if (showIframe) {
      return (
        <div className={`flex flex-1 animate-in fade-in duration-200 ${focusMode ? "" : "px-6 pb-6"}`}>
          <DocumentIframe googleDriveId={page!.googleDriveId} />
        </div>
      );
    }

    if (!isContentReady) {
      return <ContentLoadingSkeleton />;
    }

    if (content?.blocks && !isDocEmpty(content.blocks)) {
      return (
        <div className="px-6 pb-6 animate-in fade-in duration-200">
          <div className="mx-auto max-w-3xl py-4">
            <DocViewer blocks={content.blocks} />
          </div>
        </div>
      );
    }

    if (content?.blocks !== undefined) {
      return (
        <div className="flex-1 flex items-center justify-center animate-in fade-in duration-300">
          <div className="flex flex-col items-center gap-5">
            <div className="empty-doc-icon">
              <FileText className="size-8 text-muted-foreground" />
            </div>
            <div className="text-center">
              <h3 className="text-base font-medium text-foreground">
                This document is empty
              </h3>
              <p className="mt-1 text-sm text-muted-foreground">
                Start writing by switching to edit mode
              </p>
            </div>
            <button
              type="button"
              onClick={() => setIsEditing(true)}
              className="empty-doc-edit-btn"
            >
              <Pencil className="size-4" />
              Open editor
            </button>
            <div className="empty-doc-hint">
              <span className="empty-doc-hint-arrow" />
              <span className="text-xs text-muted-foreground">
                or click <Pencil className="inline size-3 mx-0.5" /> in the toolbar above
              </span>
            </div>
          </div>
        </div>
      );
    }

    return (
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
    );
  }

  async function handleChangeIcon(icon: string | null) {
    start();
    try {
      await api.updatePage(pageId, { icon });
      queryClient.invalidateQueries({ queryKey: ["pages"] });
    } catch {
      toast.error("Failed to update icon");
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
    } catch {
      toast.error("Failed to rename page");
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
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  onClick={() => window.dispatchEvent(new KeyboardEvent("keydown", { key: "k", metaKey: true }))}
                  className={`${TOOLBAR_BUTTON_CLASS} ${TOOLBAR_BUTTON_IDLE}`}
                >
                  <Search className="size-4" />
                </button>
              </TooltipTrigger>
              <TooltipContent side="bottom">Search (⌘K)</TooltipContent>
            </Tooltip>
            {!alwaysIframe && !isPending && (
              <EditToggleButton
                isEditing={isEditing}
                onToggle={() => setIsEditing(!isEditing)}
                highlight={docEmpty}
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
            {!alwaysIframe && !isPending && (
              <EditToggleButton
                isEditing={isEditing}
                onToggle={() => setIsEditing(!isEditing)}
                highlight={docEmpty}
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
        {renderContent()}
      </div>
    </div>
  );
}

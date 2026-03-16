"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { useLoading } from "@/components/loading-bar";
import { usePageTree } from "@/hooks/use-page-tree";
import { useWorkspaces } from "@/hooks/use-workspaces";
import { getPageBreadcrumbs } from "@/lib/page-utils";
import type { Page, Workspace, DocTab } from "@/types";

function isDocEmpty(tabs: DocTab[] | undefined): boolean {
  if (!tabs || tabs.length === 0) return true;
  return tabs.every((tab) =>
    tab.blocks.every(
      (b) =>
        b.type === "paragraph" &&
        (!b.runs || b.runs.length === 0 || b.runs.every((r) => !r.text?.trim())),
    ),
  );
}

interface PageContent {
  tabs: DocTab[];
  cached: boolean;
}

export interface UseDocumentDataReturn {
  // Page metadata
  page: Page | undefined;
  isLoading: boolean;
  error: Error | null;

  // Content
  content: PageContent | undefined;
  isContentLoading: boolean;
  isContentFetching: boolean;
  isContentReady: boolean;
  refetchContent: () => void;

  // Derived state
  isPending: boolean;
  docEmpty: boolean;
  workspace: Workspace | null;
  breadcrumbs: Page[];
  allPages: Page[];
  workspaces: Workspace[];

  // Pending state
  pendingElapsed: number;
  retrying: boolean;
  handleRetry: () => Promise<void>;
  handleDeleteStale: () => Promise<void>;

  // Page actions
  handleChangeIcon: (icon: string | null) => Promise<void>;
  handleRename: (newTitle: string) => Promise<void>;

  // Content refresh (for edit→view transitions)
  forceRefreshContent: () => void;
}

export function useDocumentData(pageId: string): UseDocumentDataReturn {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { start, done } = useLoading();

  // ── Page metadata query ──
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

  // ── Content query ──
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

  // ── Related data ──
  const { data: allPages = [] } = usePageTree();
  const { data: workspaces = [] } = useWorkspaces();

  // ── Pending state tracking ──
  const isPending = page?.googleDriveId?.startsWith("pending-") ?? false;
  const wasPendingRef = useRef(false);
  const [pendingElapsed, setPendingElapsed] = useState(0);

  useEffect(() => {
    if (!isPending) {
      setPendingElapsed(0);
      // Page just became ready — refetch content
      if (wasPendingRef.current) {
        queryClient.invalidateQueries({ queryKey: ["page-content", pageId] });
      }
      wasPendingRef.current = false;
      return;
    }
    wasPendingRef.current = true;
    setPendingElapsed(0);
    const interval = setInterval(() => setPendingElapsed((s) => s + 1), 1000);
    return () => clearInterval(interval);
  }, [isPending, pageId, queryClient]);

  // ── Derived state ──
  const isContentReady = !isContentLoading && !isContentFetching;
  const docEmpty = isContentReady && isDocEmpty(content?.tabs);

  const workspace = page?.workspaceId
    ? (workspaces.find((w) => w.id === page.workspaceId) ?? null)
    : null;

  const breadcrumbs = page ? getPageBreadcrumbs(page.id, allPages) : [];

  // ── Retry / delete stale ──
  const [retrying, setRetrying] = useState(false);

  const handleRetry = useCallback(async () => {
    setRetrying(true);
    setPendingElapsed(0);
    try {
      await api.retryPage(pageId);
      queryClient.invalidateQueries({ queryKey: ["pages", pageId] });
      queryClient.invalidateQueries({ queryKey: ["page-content", pageId] });
    } catch {
      // Failed — polling will show stale state again after 30s
    } finally {
      setRetrying(false);
    }
  }, [pageId, queryClient]);

  const handleDeleteStale = useCallback(async () => {
    try {
      await api.deletePage(pageId);
      queryClient.invalidateQueries({ queryKey: ["pages"] });
      router.replace("/");
    } catch {
      toast.error("Failed to delete page");
    }
  }, [pageId, queryClient, router]);

  // ── Page actions ──
  const handleChangeIcon = useCallback(
    async (icon: string | null) => {
      start();
      try {
        await api.updatePage(pageId, { icon });
        queryClient.invalidateQueries({ queryKey: ["pages"] });
      } catch {
        toast.error("Failed to update icon");
      } finally {
        done();
      }
    },
    [pageId, queryClient, start, done],
  );

  const handleRename = useCallback(
    async (newTitle: string) => {
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
    },
    [pageId, queryClient, start, done],
  );

  // ── Force-refresh content (for edit→view transitions) ──
  const forceRefreshContent = useCallback(() => {
    forceRefreshRef.current = true;
    queryClient.removeQueries({ queryKey: ["page-content", pageId] });
    // Give React Query a tick to process the removal, then refetch
    setTimeout(() => refetchContent(), 50);
  }, [pageId, queryClient, refetchContent]);

  // ── Auto-refresh content when tab becomes visible ──
  useEffect(() => {
    function handleVisibilityChange() {
      if (document.visibilityState === "visible" && page && !isPending) {
        forceRefreshContent();
      }
    }
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => document.removeEventListener("visibilitychange", handleVisibilityChange);
  }, [page, isPending, forceRefreshContent]);

  // ── Redirect on not found ──
  useEffect(() => {
    if (!isLoading && (error || !page)) {
      router.replace("/");
    }
  }, [isLoading, error, page, router]);

  return {
    page,
    isLoading,
    error: error as Error | null,

    content,
    isContentLoading,
    isContentFetching,
    isContentReady,
    refetchContent,

    isPending,
    docEmpty,
    workspace,
    breadcrumbs,
    allPages,
    workspaces,

    pendingElapsed,
    retrying,
    handleRetry,
    handleDeleteStale,

    handleChangeIcon,
    handleRename,

    forceRefreshContent,
  };
}

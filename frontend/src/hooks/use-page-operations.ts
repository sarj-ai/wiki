"use client";

import { useCallback, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { useLoading } from "@/components/loading-bar";
import { findPageById } from "@/lib/page-utils";
import type { Page } from "@/types";

/**
 * Page CRUD handlers used by the sidebar: create, rename, delete, move, and
 * icon changes. Manages related transient state (renaming ID, adding-to
 * indicator, etc.).
 */
export function usePageOperations(allPages: Page[], activePageId: string | null) {
  const queryClient = useQueryClient();
  const router = useRouter();
  const { start, done } = useLoading();

  // Use refs for values accessed inside async callbacks to avoid stale closures
  const allPagesRef = useRef(allPages);
  allPagesRef.current = allPages;
  const activePageIdRef = useRef(activePageId);
  activePageIdRef.current = activePageId;

  const [renamingPageId, setRenamingPageId] = useState<string | null>(null);
  const [deletingPageId, setDeletingPageId] = useState<string | null>(null);
  const [addingTo, setAddingTo] = useState<string | null>(null);
  const [addingChildParentId, setAddingChildParentId] = useState<string | null>(
    null,
  );
  const [movingPageId, setMovingPageId] = useState<string | null>(null);
  const [duplicatingPageId, setDuplicatingPageId] = useState<string | null>(null);

  // --- Derived values for delete confirmation dialog ---
  // Memoized to avoid tree traversal on every render when no delete dialog is open.
  const { deletingPage, deleteChildCount } = useMemo(() => {
    if (!deletingPageId) return { deletingPage: null, deleteChildCount: 0 };

    function countDescendants(page: Page): number {
      let count = 0;
      for (const child of page.children) {
        count += 1 + countDescendants(child);
      }
      return count;
    }

    const page = findPageById(deletingPageId, allPages) ?? null;
    return { deletingPage: page, deleteChildCount: page ? countDescendants(page) : 0 };
  }, [deletingPageId, allPages]);

  // --- Handlers (wrapped in useCallback to prevent stale closures) ---

  const handleDelete = useCallback(async () => {
    if (!deletingPageId) return;
    const pageId = deletingPageId;
    setDeletingPageId(null);
    start();
    try {
      await api.deletePage(pageId);
      queryClient.invalidateQueries({ queryKey: ["pages"] });
      if (activePageIdRef.current === pageId) {
        router.push("/");
      }
    } catch {
      toast.error("Failed to delete page");
    } finally {
      done();
    }
  }, [deletingPageId, queryClient, router, start, done]);

  const handleRename = useCallback(async (pageId: string, newTitle: string) => {
    try {
      await api.updatePage(pageId, { title: newTitle });
      queryClient.invalidateQueries({ queryKey: ["pages"] });
      queryClient.invalidateQueries({ queryKey: ["pages", pageId] });
    } catch {
      toast.error("Failed to rename page");
    }
  }, [queryClient]);

  const handleChangeIcon = useCallback(async (pageId: string, icon: string | null) => {
    try {
      await api.updatePage(pageId, { icon });
      queryClient.invalidateQueries({ queryKey: ["pages"] });
      queryClient.invalidateQueries({ queryKey: ["pages", pageId] });
    } catch {
      toast.error("Failed to update icon");
    }
  }, [queryClient]);

  const handleMove = useCallback(async (
    pageId: string,
    newParentId: string | null,
    newIndex: number,
  ) => {
    setMovingPageId(pageId);
    start();
    try {
      // Find siblings under the new parent to compute a proper sort order
      const allPages = allPagesRef.current;
      const siblings = (newParentId
        ? findPageById(newParentId, allPages)?.children ?? []
        : allPages
      ).filter((p) => p.id !== pageId).sort((a, b) => a.sortOrder - b.sortOrder);

      let sortOrder: number;
      if (siblings.length === 0) {
        sortOrder = 1;
      } else if (newIndex <= 0) {
        sortOrder = siblings[0].sortOrder - 1;
      } else if (newIndex >= siblings.length) {
        sortOrder = siblings[siblings.length - 1].sortOrder + 1;
      } else {
        const before = siblings[newIndex - 1].sortOrder;
        const after = siblings[newIndex].sortOrder;
        sortOrder = (before + after) / 2;
      }

      await api.updatePage(pageId, { parentId: newParentId, sortOrder });
      await queryClient.invalidateQueries({ queryKey: ["pages"] });
    } catch {
      toast.error("Failed to move page");
    } finally {
      setMovingPageId(null);
      done();
    }
  }, [queryClient, start, done]);

  const handleAddChild = useCallback(async (parentId: string) => {
    setAddingChildParentId(parentId);
    try {
      const parentPage = findPageById(parentId, allPagesRef.current);
      const page = await api.createPage({
        title: "Untitled",
        parentId,
        workspaceId: parentPage?.workspaceId ?? undefined,
      });
      await queryClient.invalidateQueries({ queryKey: ["pages"] });
      setRenamingPageId(page.id);
    } catch {
      toast.error("Failed to create page");
    } finally {
      setAddingChildParentId(null);
    }
  }, [queryClient]);

  const handleAddRootPage = useCallback(async (workspaceId?: string) => {
    setAddingTo(workspaceId ?? "no-workspace");
    try {
      const page = await api.createPage({
        title: "Untitled",
        workspaceId,
      });
      await queryClient.invalidateQueries({ queryKey: ["pages"] });
      setRenamingPageId(page.id);
    } catch {
      toast.error("Failed to create page");
    } finally {
      setAddingTo(null);
    }
  }, [queryClient]);

  const handleAddPrivatePage = useCallback(async () => {
    setAddingTo("private");
    try {
      const page = await api.createPage({ title: "Untitled" });
      await queryClient.invalidateQueries({ queryKey: ["pages"] });
      setRenamingPageId(page.id);
    } catch {
      toast.error("Failed to create page");
    } finally {
      setAddingTo(null);
    }
  }, [queryClient]);

  const handleDuplicate = useCallback(async (pageId: string) => {
    setDuplicatingPageId(pageId);
    start();
    try {
      const newPage = await api.duplicatePage(pageId);
      await queryClient.invalidateQueries({ queryKey: ["pages"] });
      toast.success("Page duplicated");
      router.push(`/docs/${newPage.id}`);
    } catch {
      toast.error("Failed to duplicate page");
    } finally {
      setDuplicatingPageId(null);
      done();
    }
  }, [queryClient, router, start, done]);

  const handleMoveToWorkspace = useCallback(async (pageId: string, workspaceId: string) => {
    start();
    try {
      await api.moveToWorkspace(pageId, workspaceId);
      await queryClient.invalidateQueries({ queryKey: ["pages"] });
      toast.success("Page moved to workspace");
    } catch {
      toast.error("Failed to move page");
    } finally {
      done();
    }
  }, [queryClient, start, done]);

  const handleRenamingDone = useCallback(() => {
    if (renamingPageId) {
      router.push(`/docs/${renamingPageId}`);
    }
    setRenamingPageId(null);
  }, [renamingPageId, router]);

  return {
    // State
    renamingPageId,
    deletingPageId,
    setDeletingPageId,
    addingTo,
    addingChildParentId,
    movingPageId,
    duplicatingPageId,
    // Delete dialog helpers
    deletingPage,
    deleteChildCount,
    // Handlers
    handleDelete,
    handleRename,
    handleChangeIcon,
    handleMove,
    handleAddChild,
    handleAddRootPage,
    handleAddPrivatePage,
    handleRenamingDone,
    handleDuplicate,
    handleMoveToWorkspace,
  } as const;
}

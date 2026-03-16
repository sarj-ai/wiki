"use client";

import { useCallback, useState } from "react";
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

  const [renamingPageId, setRenamingPageId] = useState<string | null>(null);
  const [deletingPageId, setDeletingPageId] = useState<string | null>(null);
  const [addingTo, setAddingTo] = useState<string | null>(null);
  const [addingChildParentId, setAddingChildParentId] = useState<string | null>(
    null,
  );
  const [movingPageId, setMovingPageId] = useState<string | null>(null);

  // --- Derived values for delete confirmation dialog ---

  function countDescendants(page: Page): number {
    let count = 0;
    for (const child of page.children) {
      count += 1 + countDescendants(child);
    }
    return count;
  }

  const deletingPage = deletingPageId
    ? findPageById(deletingPageId, allPages)
    : null;
  const deleteChildCount = deletingPage ? countDescendants(deletingPage) : 0;

  // --- Handlers ---

  async function handleDelete() {
    if (!deletingPageId) return;
    const pageId = deletingPageId;
    setDeletingPageId(null);
    start();
    try {
      await api.deletePage(pageId);
      queryClient.invalidateQueries({ queryKey: ["pages"] });
      if (activePageId === pageId) {
        router.push("/");
      }
    } catch {
      toast.error("Failed to delete page");
    } finally {
      done();
    }
  }

  async function handleRename(pageId: string, newTitle: string) {
    try {
      await api.updatePage(pageId, { title: newTitle });
      queryClient.invalidateQueries({ queryKey: ["pages"] });
      queryClient.invalidateQueries({ queryKey: ["pages", pageId] });
    } catch {
      toast.error("Failed to rename page");
    }
  }

  async function handleChangeIcon(pageId: string, icon: string | null) {
    try {
      await api.updatePage(pageId, { icon });
      queryClient.invalidateQueries({ queryKey: ["pages"] });
      queryClient.invalidateQueries({ queryKey: ["pages", pageId] });
    } catch {
      toast.error("Failed to update icon");
    }
  }

  async function handleMove(
    pageId: string,
    newParentId: string | null,
    newIndex: number,
  ) {
    setMovingPageId(pageId);
    start();
    try {
      const sortOrder = newIndex + 0.5;
      await api.updatePage(pageId, { parentId: newParentId, sortOrder });
      await queryClient.invalidateQueries({ queryKey: ["pages"] });
    } catch {
      toast.error("Failed to move page");
    } finally {
      setMovingPageId(null);
      done();
    }
  }

  async function handleAddChild(parentId: string) {
    setAddingChildParentId(parentId);
    try {
      const parentPage = findPageById(parentId, allPages);
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
  }

  async function handleAddRootPage(workspaceId?: string) {
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
  }

  async function handleAddPrivatePage() {
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
  }

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
  } as const;
}

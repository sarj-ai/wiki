"use client";

import { useCallback, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import { toast } from "sonner";
import { api } from "@/lib/api";
import type { Workspace } from "@/types";

/**
 * Workspace-level sidebar logic: drag-to-reorder, icon selection, and sync.
 */
export function useWorkspaceManagement(workspaces: Workspace[]) {
  const queryClient = useQueryClient();
  const [isSyncing, setIsSyncing] = useState(false);
  const [emojiPickerWorkspaceId, setEmojiPickerWorkspaceId] = useState<
    string | null
  >(null);

  // DnD sensors for workspace reorder
  const workspaceSensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 8 } }),
  );

  const workspaceIds = useMemo(
    () => workspaces.map((ws) => ws.id),
    [workspaces],
  );

  const handleWorkspaceReorder = useCallback(async (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const oldIndex = workspaces.findIndex((ws) => ws.id === active.id);
    const newIndex = workspaces.findIndex((ws) => ws.id === over.id);
    if (oldIndex === -1 || newIndex === -1) return;

    // Optimistic reorder
    const reordered = [...workspaces];
    const [moved] = reordered.splice(oldIndex, 1);
    reordered.splice(newIndex, 0, moved);
    const updated = reordered.map((ws, i) => ({ ...ws, sortOrder: i }));
    queryClient.setQueryData<Workspace[]>(["workspaces"], updated);

    try {
      await api.reorderWorkspaces(updated.map((ws) => ws.id));
    } catch {
      toast.error("Failed to reorder workspaces");
      queryClient.invalidateQueries({ queryKey: ["workspaces"] });
    }
  }, [workspaces, queryClient]);

  const handleWorkspaceIcon = useCallback(async (
    workspaceId: string,
    icon: string | null,
  ) => {
    // Optimistic update
    queryClient.setQueryData<Workspace[]>(["workspaces"], (old) =>
      old?.map((ws) => (ws.id === workspaceId ? { ...ws, icon } : ws)),
    );
    try {
      await api.updateWorkspace(workspaceId, { icon });
    } catch {
      toast.error("Failed to update workspace icon");
      queryClient.invalidateQueries({ queryKey: ["workspaces"] });
    }
  }, [queryClient]);

  const handleSync = useCallback(async (force?: boolean) => {
    setIsSyncing(true);
    try {
      const [, result] = await Promise.all([
        api.syncWorkspaces(),
        api.syncPages(force),
      ]);
      queryClient.invalidateQueries({ queryKey: ["workspaces"] });
      queryClient.invalidateQueries({ queryKey: ["pages"] });
      queryClient.invalidateQueries({ queryKey: ["page-content"] });
      const parts: string[] = [];
      if (result.added) parts.push(`${result.added} added`);
      if (result.updated) parts.push(`${result.updated} updated`);
      if (result.removed) parts.push(`${result.removed} removed`);
      toast.success(
        parts.length ? `Synced: ${parts.join(", ")}` : "Everything up to date",
      );
    } catch {
      toast.error("Failed to sync");
    } finally {
      setIsSyncing(false);
    }
  }, [queryClient]);

  const handleForceSync = useCallback(() => handleSync(true), [handleSync]);

  return {
    isSyncing,
    handleSync,
    handleForceSync,
    workspaceSensors,
    workspaceIds,
    handleWorkspaceReorder,
    emojiPickerWorkspaceId,
    setEmojiPickerWorkspaceId,
    handleWorkspaceIcon,
  } as const;
}

"use client";

import { useState } from "react";
import type { Page, Workspace } from "@/types";
import { useLoading } from "@/components/loading-bar";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Check, Loader2 } from "lucide-react";

interface WorkspaceShareProps {
  page: Page;
  workspaces: Workspace[];
  onOpenChange: (open: boolean) => void;
}

export function WorkspaceShare({
  page,
  workspaces,
  onOpenChange,
}: WorkspaceShareProps) {
  const { start, done } = useLoading();
  const queryClient = useQueryClient();
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState(
    page.workspaceId ?? (workspaces[0]?.id ?? ""),
  );
  const [isPublishing, setIsPublishing] = useState(false);

  const isAlreadyPublished = !page.isPrivate && !!page.workspaceId;
  const publishedWorkspace = isAlreadyPublished
    ? workspaces.find((w) => w.id === page.workspaceId)
    : null;

  async function handlePublish() {
    if (!selectedWorkspaceId) return;
    setIsPublishing(true);
    start();
    try {
      await api.publishPage(page.id, selectedWorkspaceId);
      await queryClient.invalidateQueries({ queryKey: ["pages"] });
      await queryClient.invalidateQueries({ queryKey: ["pages", page.id] });
      setTimeout(() => onOpenChange(false), 800);
    } catch {
      // error handled by loading bar
    } finally {
      setIsPublishing(false);
      done();
    }
  }

  if (workspaces.length === 0) return null;

  // Already published — show static state
  if (isAlreadyPublished) {
    return (
      <div className="px-6 py-4">
        <div className="flex items-center gap-2 text-sm">
          <Check className="size-4 text-green-600 dark:text-green-400" />
          <span>
            Published to{" "}
            <span className="font-medium">
              {publishedWorkspace?.name ?? "workspace"}
            </span>
          </span>
        </div>
      </div>
    );
  }

  // Private — show publish flow
  return (
    <div className="px-6 py-4">
      <div className="flex flex-col gap-0.5 mb-3">
        <span className="text-sm font-medium">Publish to workspace</span>
        <span className="text-xs text-muted-foreground">
          Make this page visible to all workspace members
        </span>
      </div>
      <div className="flex items-center gap-2">
        <Select
          value={selectedWorkspaceId}
          onValueChange={setSelectedWorkspaceId}
        >
          <SelectTrigger className="flex-1 h-9" size="sm">
            <SelectValue placeholder="Select a workspace..." />
          </SelectTrigger>
          <SelectContent>
            {workspaces.map((ws) => (
              <SelectItem key={ws.id} value={ws.id}>
                {ws.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button
          size="sm"
          onClick={handlePublish}
          disabled={!selectedWorkspaceId || isPublishing}
        >
          {isPublishing ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            "Publish"
          )}
        </Button>
      </div>
    </div>
  );
}

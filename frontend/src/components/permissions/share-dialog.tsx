"use client";

import { useState, useEffect } from "react";
import type { Page, Workspace, PagePermission, Permission } from "@/types";
import { useAuth } from "@/components/auth-provider";
import { useWorkspaces } from "@/hooks/use-workspaces";
import { useLoading } from "@/components/loading-bar";
import { api } from "@/lib/api";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { Link2 } from "lucide-react";
import { UserSearchInput } from "./user-search-input";
import { PermissionList } from "./permission-list";
import { WorkspaceShare } from "./workspace-share";

interface ShareDialogProps {
  page: Page;
  workspace: Workspace | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function ShareDialog({
  page,
  workspace,
  open,
  onOpenChange,
}: ShareDialogProps) {
  const { user } = useAuth();
  const { data: workspaces = [] } = useWorkspaces();
  const { start, done } = useLoading();

  const [permissions, setPermissions] = useState<PagePermission[]>([]);
  const [isLoadingPermissions, setIsLoadingPerms] = useState(false);
  const [copied, setCopied] = useState(false);

  // Fetch permissions when dialog opens
  useEffect(() => {
    if (!open) return;
    setIsLoadingPerms(true);
    api
      .getPagePermissions(page.id)
      .then(setPermissions)
      .catch(() => setPermissions([]))
      .finally(() => setIsLoadingPerms(false));
  }, [open, page.id]);

  function handleUserSelected(newPerm: PagePermission) {
    setPermissions((prev) => [...prev, newPerm]);
  }

  async function handleChangePermission(permId: string, permission: Permission) {
    const prev = permissions;
    // Optimistic update
    setPermissions((perms) =>
      perms.map((p) => (p.id === permId ? { ...p, permission } : p))
    );
    try {
      await api.updatePermission(permId, permission);
    } catch {
      setPermissions(prev); // revert
    }
  }

  async function handleRemovePermission(permId: string) {
    start();
    try {
      await api.revokePermission(permId);
      setPermissions((prev) => prev.filter((p) => p.id !== permId));
    } catch {
      // revert would go here
    } finally {
      done();
    }
  }

  async function handleCopyLink() {
    try {
      await navigator.clipboard.writeText(
        `${window.location.origin}/docs/${encodeURIComponent(page.id)}`
      );
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard access may be denied in some contexts
    }
  }

  const visibilityLabel = !page.isPrivate && page.workspaceId
    ? `Shared with ${workspaces.find((w) => w.id === page.workspaceId)?.name ?? "a workspace"}`
    : "My Drafts - only you and invited people";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[480px] gap-0 p-0">
        {/* Header */}
        <DialogHeader className="px-6 pt-6 pb-4">
          <DialogTitle className="truncate">
            Share &ldquo;{page.title}&rdquo;
          </DialogTitle>
          <DialogDescription>{visibilityLabel}</DialogDescription>
        </DialogHeader>

        <Separator />

        {/* Add people section */}
        <div className="px-6 py-4">
          {open && (
            <UserSearchInput
              key={open ? "open" : "closed"}
              pageId={page.id}
              pageType={page.type}
              permissions={permissions}
              onSelectUser={handleUserSelected}
            />
          )}
        </div>

        <Separator />

        {/* Current access list */}
        <div className="px-6 py-4">
          <PermissionList
            permissions={permissions}
            owner={
              page.createdBy && page.createdByEmail
                ? {
                    id: page.createdBy,
                    email: page.createdByEmail,
                    displayName: page.createdByName ?? null,
                    avatarUrl: page.createdByAvatar ?? null,
                  }
                : null
            }
            ownerFallbackWorkspace={
              !page.createdBy && page.workspaceId
                ? workspaces.find((w) => w.id === page.workspaceId) ?? null
                : null
            }
            workspaces={workspaces}
            isLoading={isLoadingPermissions}
            onRemove={handleRemovePermission}
            onChangePermission={handleChangePermission}
          />
        </div>

        <Separator />

        {/* Visibility toggle */}
        {workspaces.length > 0 && (
          <>
            <WorkspaceShare
              page={page}
              workspaces={workspaces}
              onOpenChange={onOpenChange}
            />
            <Separator />
          </>
        )}

        {/* Footer */}
        <DialogFooter className="px-6 py-4 sm:justify-between">
          <Button
            variant="outline"
            size="sm"
            onClick={handleCopyLink}
            className="gap-2"
          >
            <Link2 className="size-4" />
            {copied ? "Copied!" : "Copy link"}
          </Button>
          <Button size="sm" onClick={() => onOpenChange(false)}>
            Done
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

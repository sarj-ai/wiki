"use client";

import { useState, useMemo, useEffect } from "react";
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
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import {
  Avatar,
  AvatarFallback,
  AvatarImage,
} from "@/components/ui/avatar";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Link2,
  X,
  Users,
  Crown,
  Loader2,
} from "lucide-react";

interface ShareDialogProps {
  page: Page;
  workspace: Workspace | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

function getInitials(name: string): string {
  return name
    .split(" ")
    .map((n) => n[0])
    .join("")
    .toUpperCase()
    .slice(0, 2);
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
  const [isLoadingPerms, setIsLoadingPerms] = useState(false);
  const [isSharedWithWorkspace, setIsSharedWithWorkspace] = useState(!page.isPrivate);
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState<string>(
    page.workspaceId ?? ""
  );
  const [searchQuery, setSearchQuery] = useState("");
  const [invitePermission, setInvitePermission] = useState<Permission>("view");
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

  // No search suggestions without a user directory endpoint yet
  const suggestions = useMemo(() => {
    // Future: call an API to search users by email
    return [];
  }, []);

  async function handleChangePermission(permId: string, newPerm: Permission) {
    // Update locally for instant feedback
    setPermissions((prev) =>
      prev.map((p) => (p.id === permId ? { ...p, permission: newPerm } : p))
    );
    // TODO: backend doesn't have a PATCH permission endpoint yet
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

  function handleToggleWorkspaceSharing(checked: boolean) {
    setIsSharedWithWorkspace(checked);
    if (!checked) {
      setPermissions((prev) => prev.filter((p) => p.workspaceId === null));
      setSelectedWorkspaceId("");
    }
  }

  async function handleSelectWorkspace(wsId: string) {
    setSelectedWorkspaceId(wsId);
    const ws = workspaces.find((w) => w.id === wsId);
    if (!ws) return;

    start();
    try {
      const perm = await api.grantPermission({
        pageId: page.id,
        workspaceId: wsId,
        permission: "edit",
      });

      setPermissions((prev) => {
        const withoutWorkspace = prev.filter((p) => p.workspaceId === null);
        return [...withoutWorkspace, perm];
      });
    } catch {
      // handle error
    } finally {
      done();
    }
  }

  function handleCopyLink() {
    navigator.clipboard.writeText(
      `${window.location.origin}/docs/${page.id}`
    );
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  const visibilityLabel = isSharedWithWorkspace
    ? `Shared with ${workspaces.find((w) => w.id === selectedWorkspaceId)?.name ?? "a workspace"}`
    : "Private - only you and invited people";

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
          <div className="flex gap-2">
            <div className="relative flex-1">
              <Input
                placeholder="Search people by name or email..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="h-9"
              />
              {suggestions.length > 0 && (
                <div className="absolute top-full left-0 right-0 z-50 mt-1 rounded-md border bg-popover p-1 shadow-md">
                  <p className="px-2 py-1.5 text-xs text-muted-foreground">
                    User search coming soon
                  </p>
                </div>
              )}
            </div>
            <Select
              value={invitePermission}
              onValueChange={(v) => setInvitePermission(v as Permission)}
            >
              <SelectTrigger className="w-[110px] h-9" size="sm">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="view">Can view</SelectItem>
                <SelectItem value="edit">Can edit</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>

        <Separator />

        {/* Current access list */}
        <div className="px-6 py-4">
          <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-3">
            Who has access
          </p>
          <div className="flex flex-col gap-1 max-h-[200px] overflow-y-auto">
            {/* Owner row */}
            {user && (
              <div className="flex items-center gap-3 rounded-md px-2 py-2">
                <Avatar className="size-7">
                  {user.avatarUrl && (
                    <AvatarImage src={user.avatarUrl} alt={user.displayName ?? ""} />
                  )}
                  <AvatarFallback className="text-xs">
                    {getInitials(user.displayName ?? user.email)}
                  </AvatarFallback>
                </Avatar>
                <div className="flex flex-col min-w-0 flex-1">
                  <span className="text-sm font-medium truncate">
                    {user.displayName ?? user.email}
                  </span>
                  <span className="text-xs text-muted-foreground truncate">
                    {user.email}
                  </span>
                </div>
                <div className="flex items-center gap-1.5 text-xs text-muted-foreground shrink-0">
                  <Crown className="size-3.5" />
                  <span>Owner</span>
                </div>
              </div>
            )}

            {isLoadingPerms && (
              <div className="flex items-center justify-center py-4">
                <Loader2 className="size-4 animate-spin text-muted-foreground" />
              </div>
            )}

            {/* Permission entries */}
            {permissions.map((perm) => (
              <div
                key={perm.id}
                className="flex items-center gap-3 rounded-md px-2 py-2 group hover:bg-accent/50 transition-colors"
              >
                <Avatar className="size-7">
                  <AvatarFallback className="text-xs">
                    {perm.workspaceId ? (
                      <Users className="size-3.5" />
                    ) : (
                      getInitials(perm.displayName ?? "?")
                    )}
                  </AvatarFallback>
                </Avatar>
                <div className="flex flex-col min-w-0 flex-1">
                  <span className="text-sm font-medium truncate">
                    {perm.displayName ?? perm.userId ?? "Unknown"}
                  </span>
                  <span className="text-xs text-muted-foreground truncate">
                    {perm.workspaceId ? "All members" : perm.email}
                  </span>
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <Select
                    value={perm.permission}
                    onValueChange={(v) =>
                      handleChangePermission(perm.id, v as Permission)
                    }
                  >
                    <SelectTrigger className="h-7 w-[100px] text-xs border-none shadow-none" size="sm">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="view">Can view</SelectItem>
                      <SelectItem value="edit">Can edit</SelectItem>
                    </SelectContent>
                  </Select>
                  <button
                    type="button"
                    onClick={() => handleRemovePermission(perm.id)}
                    className="size-7 inline-flex items-center justify-center rounded-md text-muted-foreground opacity-0 group-hover:opacity-100 hover:text-foreground hover:bg-accent transition-all"
                    aria-label="Remove"
                  >
                    <X className="size-3.5" />
                  </button>
                </div>
              </div>
            ))}

            {!isLoadingPerms && permissions.length === 0 && (
              <p className="text-sm text-muted-foreground py-2 px-2">
                No one else has access yet.
              </p>
            )}
          </div>
        </div>

        <Separator />

        {/* Visibility toggle */}
        {workspaces.length > 0 && (
          <>
            <div className="px-6 py-4">
              <div className="flex items-center justify-between">
                <div className="flex flex-col gap-0.5">
                  <Label htmlFor="workspace-sharing" className="text-sm font-medium">
                    Share with workspace
                  </Label>
                  <span className="text-xs text-muted-foreground">
                    Make this page visible to all workspace members
                  </span>
                </div>
                <Switch
                  id="workspace-sharing"
                  checked={isSharedWithWorkspace}
                  onCheckedChange={handleToggleWorkspaceSharing}
                />
              </div>
              {isSharedWithWorkspace && (
                <div className="mt-3">
                  <Select
                    value={selectedWorkspaceId}
                    onValueChange={handleSelectWorkspace}
                  >
                    <SelectTrigger className="w-full h-9" size="sm">
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
                </div>
              )}
            </div>
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
          <Button
            size="sm"
            onClick={() => onOpenChange(false)}
          >
            Done
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

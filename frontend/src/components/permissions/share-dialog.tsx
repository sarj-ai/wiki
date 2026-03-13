"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import type { Page, Workspace, PagePermission, Permission, UserSearchResult } from "@/types";
import { useAuth } from "@/components/auth-provider";
import { useWorkspaces } from "@/hooks/use-workspaces";
import { useLoading } from "@/components/loading-bar";
import { useQueryClient } from "@tanstack/react-query";
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
  Check,
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
    .map((part) => part[0])
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
  const queryClient = useQueryClient();

  const [permissions, setPermissions] = useState<PagePermission[]>([]);
  const [isLoadingPermissions, setIsLoadingPerms] = useState(false);
  const [isSharedWithWorkspace, setIsSharedWithWorkspace] = useState(!page.isPrivate);
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState<string>(
    page.workspaceId ?? ""
  );
  const [searchQuery, setSearchQuery] = useState("");
  const [invitePermission, setInvitePermission] = useState<Permission>("view");
  const [copied, setCopied] = useState(false);
  const [publishSuccess, setPublishSuccess] = useState(false);

  // User search state
  const [suggestions, setSuggestions] = useState<UserSearchResult[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [grantingUserId, setGrantingUserId] = useState<string | null>(null);
  const searchRef = useRef<HTMLDivElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(null);
  const searchCounterRef = useRef(0);

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

  // Reset search state when dialog closes
  useEffect(() => {
    if (!open) {
      setSearchQuery("");
      setSuggestions([]);
      setShowSuggestions(false);
    }
  }, [open]);

  // Debounced user search with request counter to prevent stale results
  const handleSearchChange = useCallback((value: string) => {
    setSearchQuery(value);
    if (debounceRef.current) clearTimeout(debounceRef.current);

    if (value.trim().length < 2) {
      setSuggestions([]);
      setShowSuggestions(false);
      return;
    }

    debounceRef.current = setTimeout(async () => {
      const requestId = ++searchCounterRef.current;
      setIsSearching(true);
      try {
        const results = await api.searchUsers(value.trim());
        // Only apply results if this is still the latest request
        if (requestId !== searchCounterRef.current) return;
        // Filter out users who already have permission on this page
        const existingUserIds = new Set(
          permissions.filter((p) => p.userId).map((p) => p.userId)
        );
        const filtered = results.filter((u) => !existingUserIds.has(u.id));
        setSuggestions(filtered);
        setShowSuggestions(true);
      } catch {
        if (requestId === searchCounterRef.current) {
          setSuggestions([]);
        }
      } finally {
        if (requestId === searchCounterRef.current) {
          setIsSearching(false);
        }
      }
    }, 300);
  }, [permissions]);

  // Close suggestions on outside click
  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (searchRef.current && !searchRef.current.contains(e.target as Node)) {
        setShowSuggestions(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  async function handleSelectUser(selectedUser: UserSearchResult) {
    setGrantingUserId(selectedUser.id);
    start();
    try {
      const newPerm = await api.grantPermission({
        pageId: page.id,
        userId: selectedUser.id,
        permission: invitePermission,
      });
      setPermissions((prev) => [...prev, newPerm]);
      setSearchQuery("");
      setSuggestions([]);
      setShowSuggestions(false);
    } catch {
      // Could show error toast
    } finally {
      setGrantingUserId(null);
      done();
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
      await api.publishPage(page.id, wsId);
      setPublishSuccess(true);
      // Refresh the sidebar tree and current page data
      await queryClient.invalidateQueries({ queryKey: ["pages"] });
      setTimeout(() => {
        setPublishSuccess(false);
        onOpenChange(false);
      }, 1500);
    } catch {
      setPublishSuccess(false);
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
            <div className="relative flex-1" ref={searchRef}>
              <Input
                placeholder="Search people by name or email..."
                value={searchQuery}
                onChange={(e) => handleSearchChange(e.target.value)}
                onFocus={() => {
                  if (suggestions.length > 0) setShowSuggestions(true);
                }}
                className="h-9"
              />
              {showSuggestions && (
                <div className="absolute top-full left-0 right-0 z-50 mt-1 rounded-md border bg-popover p-1 shadow-md max-h-[200px] overflow-y-auto">
                  {isSearching && (
                    <div className="flex items-center justify-center py-3">
                      <Loader2 className="size-4 animate-spin text-muted-foreground" />
                    </div>
                  )}
                  {!isSearching && suggestions.length === 0 && searchQuery.trim().length >= 2 && (
                    <p className="px-2 py-2 text-sm text-muted-foreground">
                      No users found
                    </p>
                  )}
                  {suggestions.map((s) => (
                    <button
                      key={s.id}
                      type="button"
                      disabled={grantingUserId === s.id}
                      onClick={() => handleSelectUser(s)}
                      className="flex w-full items-center gap-3 rounded-sm px-2 py-2 text-left hover:bg-accent transition-colors disabled:opacity-50"
                    >
                      <Avatar className="size-7">
                        {s.avatarUrl && (
                          <AvatarImage src={s.avatarUrl} alt={s.displayName ?? ""} />
                        )}
                        <AvatarFallback className="text-xs">
                          {getInitials(s.displayName ?? s.email)}
                        </AvatarFallback>
                      </Avatar>
                      <div className="flex flex-col min-w-0 flex-1">
                        <span className="text-sm font-medium truncate">
                          {s.displayName ?? s.email}
                        </span>
                        <span className="text-xs text-muted-foreground truncate">
                          {s.email}
                        </span>
                      </div>
                      {grantingUserId === s.id && (
                        <Loader2 className="size-4 animate-spin text-muted-foreground shrink-0" />
                      )}
                    </button>
                  ))}
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

            {isLoadingPermissions && (
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
                      getInitials(perm.displayName ?? perm.email ?? "?")
                    )}
                  </AvatarFallback>
                </Avatar>
                <div className="flex flex-col min-w-0 flex-1">
                  <span className="text-sm font-medium truncate">
                    {perm.workspaceId
                      ? (workspaces.find((w) => w.id === perm.workspaceId)?.name ?? perm.displayName ?? "Workspace")
                      : (perm.displayName ?? perm.email ?? "Unknown")}
                  </span>
                  <span className="text-xs text-muted-foreground truncate">
                    {perm.workspaceId ? "All members" : perm.email}
                  </span>
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <span className="h-7 px-2 inline-flex items-center text-xs text-muted-foreground">
                    {perm.permission === "edit" ? "Can edit" : "Can view"}
                  </span>
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

            {!isLoadingPermissions && permissions.length === 0 && (
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
                  {publishSuccess && (
                    <div className="mt-2 flex items-center gap-2 text-sm text-green-600 dark:text-green-400">
                      <Check className="size-4" />
                      <span>Page published to workspace</span>
                    </div>
                  )}
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

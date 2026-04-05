"use client";

import type { PagePermission, Permission, User, Workspace } from "@/types";
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
import { Users, Crown, Loader2 } from "lucide-react";
import { getInitials } from "./utils";

interface PermissionListProps {
  permissions: PagePermission[];
  owner: User | null;
  ownerFallbackWorkspace?: Workspace | null;
  workspaces: Workspace[];
  isLoading: boolean;
  onRemove: (permissionId: string) => void;
  onChangePermission: (permissionId: string, permission: Permission) => void;
}

export function PermissionList({
  permissions,
  owner,
  ownerFallbackWorkspace,
  workspaces,
  isLoading,
  onRemove,
  onChangePermission,
}: PermissionListProps) {
  return (
    <div>
      <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-3">
        Who has access
      </p>
      <div className="flex flex-col gap-1 max-h-[200px] overflow-y-auto">
        {/* Owner row */}
        {owner ? (
          <div className="flex items-center gap-3 rounded-md px-2 py-2">
            <Avatar className="size-7">
              {owner.avatarUrl && (
                <AvatarImage
                  src={owner.avatarUrl}
                  alt={owner.displayName ?? ""}
                />
              )}
              <AvatarFallback className="text-xs">
                {getInitials(owner.displayName ?? owner.email)}
              </AvatarFallback>
            </Avatar>
            <div className="flex flex-col min-w-0 flex-1">
              <span className="text-sm font-medium truncate">
                {owner.displayName ?? owner.email}
              </span>
              <span className="text-xs text-muted-foreground truncate">
                {owner.email}
              </span>
            </div>
            <span className="text-xs text-muted-foreground flex items-center gap-1.5">
              <Crown className="size-3.5" />
              Owner
            </span>
          </div>
        ) : null}

        {ownerFallbackWorkspace && (
          <div className="flex items-center gap-3 rounded-md px-2 py-2">
            <div className="flex size-7 items-center justify-center rounded-full bg-secondary">
              <Users className="size-3.5 text-secondary-foreground" />
            </div>
            <div className="flex flex-col min-w-0 flex-1">
              <span className="text-sm font-medium truncate">
                {ownerFallbackWorkspace.name}
              </span>
              <span className="text-xs text-muted-foreground truncate">
                {ownerFallbackWorkspace.googleGroupEmail ?? "All members"}
              </span>
            </div>
            <span className="text-xs text-muted-foreground flex items-center gap-1.5">
              <Users className="size-3.5" />
              Workspace
            </span>
          </div>
        )}

        {isLoading && (
          <div className="flex items-center justify-center py-4">
            <Loader2 className="size-4 animate-spin text-muted-foreground" />
          </div>
        )}

        {/* Permission entries */}
        {permissions.map((perm) => (
          <div
            key={perm.id}
            className="flex items-center gap-3 rounded-md px-2 py-2 hover:bg-accent/50 transition-colors"
          >
            <Avatar className="size-7">
              {perm.avatarUrl && (
                <AvatarImage src={perm.avatarUrl} alt={perm.displayName ?? ""} />
              )}
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
                  ? (workspaces.find((w) => w.id === perm.workspaceId)?.name ??
                    perm.displayName ??
                    "Workspace")
                  : (perm.displayName ?? perm.email ?? "Unknown")}
              </span>
              <span className="text-xs text-muted-foreground truncate">
                {perm.workspaceId ? "All members" : perm.email}
              </span>
            </div>
            <Select
              value={perm.permission}
              onValueChange={(v) => {
                if (v === "remove") {
                  onRemove(perm.id);
                } else {
                  onChangePermission(perm.id, v as Permission);
                }
              }}
            >
              <SelectTrigger className="h-7 border-0 bg-transparent text-xs text-muted-foreground shadow-none hover:bg-accent w-auto gap-1 px-2" size="sm">
                <SelectValue />
              </SelectTrigger>
              <SelectContent align="end">
                <SelectItem value="view">Can view</SelectItem>
                <SelectItem value="edit">Can edit</SelectItem>
                <SelectItem value="remove" className="text-destructive focus:text-destructive">Remove</SelectItem>
              </SelectContent>
            </Select>
          </div>
        ))}

        {!isLoading && permissions.length === 0 && !owner && !ownerFallbackWorkspace && (
          <p className="text-sm text-muted-foreground py-2 px-2">
            No one else has access yet.
          </p>
        )}
      </div>
    </div>
  );
}

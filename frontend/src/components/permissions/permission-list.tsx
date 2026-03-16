"use client";

import type { PagePermission, User, Workspace } from "@/types";
import {
  Avatar,
  AvatarFallback,
  AvatarImage,
} from "@/components/ui/avatar";
import { Users, Crown, X, Loader2 } from "lucide-react";
import { getInitials } from "./utils";

interface PermissionListProps {
  permissions: PagePermission[];
  owner: User | null;
  workspaces: Workspace[];
  isLoading: boolean;
  onRemove: (permissionId: string) => void;
}

export function PermissionList({
  permissions,
  owner,
  workspaces,
  isLoading,
  onRemove,
}: PermissionListProps) {
  return (
    <div>
      <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-3">
        Who has access
      </p>
      <div className="flex flex-col gap-1 max-h-[200px] overflow-y-auto">
        {/* Owner row */}
        {owner && (
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
            <div className="flex items-center gap-1.5 text-xs text-muted-foreground shrink-0">
              <Crown className="size-3.5" />
              <span>Owner</span>
            </div>
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
                  ? (workspaces.find((w) => w.id === perm.workspaceId)?.name ??
                    perm.displayName ??
                    "Workspace")
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
                onClick={() => onRemove(perm.id)}
                className="size-7 inline-flex items-center justify-center rounded-md text-muted-foreground opacity-0 group-hover:opacity-100 hover:text-foreground hover:bg-accent transition-all"
                aria-label="Remove"
              >
                <X className="size-3.5" />
              </button>
            </div>
          </div>
        ))}

        {!isLoading && permissions.length === 0 && (
          <p className="text-sm text-muted-foreground py-2 px-2">
            No one else has access yet.
          </p>
        )}
      </div>
    </div>
  );
}

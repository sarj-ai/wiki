"use client";

import type { PagePermission, Permission, User, Workspace } from "@/types";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
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
      <p className="text-muted-foreground mb-3 text-xs font-medium tracking-wider uppercase">
        Who has access
      </p>
      <div className="flex max-h-[200px] flex-col gap-1 overflow-y-auto">
        {/* Owner row */}
        {owner ? (
          <div className="flex items-center gap-3 rounded-md px-2 py-2">
            <Avatar className="size-7">
              {owner.avatarUrl && (
                <AvatarImage src={owner.avatarUrl} alt={owner.displayName ?? ""} />
              )}
              <AvatarFallback className="text-xs">
                {getInitials(owner.displayName ?? owner.email)}
              </AvatarFallback>
            </Avatar>
            <div className="flex min-w-0 flex-1 flex-col">
              <span className="truncate text-sm font-medium">
                {owner.displayName ?? owner.email}
              </span>
              <span className="text-muted-foreground truncate text-xs">{owner.email}</span>
            </div>
            <span className="text-muted-foreground flex items-center gap-1.5 text-xs">
              <Crown className="size-3.5" />
              Owner
            </span>
          </div>
        ) : null}

        {ownerFallbackWorkspace && (
          <div className="flex items-center gap-3 rounded-md px-2 py-2">
            <div className="bg-secondary flex size-7 items-center justify-center rounded-full">
              <Users className="text-secondary-foreground size-3.5" />
            </div>
            <div className="flex min-w-0 flex-1 flex-col">
              <span className="truncate text-sm font-medium">{ownerFallbackWorkspace.name}</span>
              <span className="text-muted-foreground truncate text-xs">
                {ownerFallbackWorkspace.googleGroupEmail ?? "All members"}
              </span>
            </div>
            <span className="text-muted-foreground flex items-center gap-1.5 text-xs">
              <Users className="size-3.5" />
              Workspace
            </span>
          </div>
        )}

        {isLoading && (
          <div className="flex items-center justify-center py-4">
            <Loader2 className="text-muted-foreground size-4 animate-spin" />
          </div>
        )}

        {/* Permission entries */}
        {permissions.map((perm) => (
          <div
            key={perm.id}
            className="hover:bg-accent/50 flex items-center gap-3 rounded-md px-2 py-2 transition-colors"
          >
            <Avatar className="size-7">
              {perm.avatarUrl && <AvatarImage src={perm.avatarUrl} alt={perm.displayName ?? ""} />}
              <AvatarFallback className="text-xs">
                {perm.workspaceId ? (
                  <Users className="size-3.5" />
                ) : (
                  getInitials(perm.displayName ?? perm.email ?? "?")
                )}
              </AvatarFallback>
            </Avatar>
            <div className="flex min-w-0 flex-1 flex-col">
              <span className="truncate text-sm font-medium">
                {perm.workspaceId
                  ? (workspaces.find((w) => w.id === perm.workspaceId)?.name ??
                    perm.displayName ??
                    "Workspace")
                  : (perm.displayName ?? perm.email ?? "Unknown")}
              </span>
              <span className="text-muted-foreground truncate text-xs">
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
              <SelectTrigger
                className="text-muted-foreground hover:bg-accent h-7 w-auto gap-1 border-0 bg-transparent px-2 text-xs shadow-none"
                size="sm"
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent align="end">
                <SelectItem value="view">Can view</SelectItem>
                <SelectItem value="edit">Can edit</SelectItem>
                <SelectItem value="remove" className="text-destructive focus:text-destructive">
                  Remove
                </SelectItem>
              </SelectContent>
            </Select>
          </div>
        ))}

        {!isLoading && permissions.length === 0 && !owner && !ownerFallbackWorkspace && (
          <p className="text-muted-foreground px-2 py-2 text-sm">No one else has access yet.</p>
        )}
      </div>
    </div>
  );
}

"use client";

import { useState } from "react";
import { useTheme } from "next-themes";
import { Sun, Moon, LogOut, RefreshCw } from "lucide-react";
import {
  SidebarFooter,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/components/auth-provider";

export function UserFooter({ onForceSync }: { onForceSync?: () => void }) {
  const { resolvedTheme, setTheme } = useTheme();
  const { user, logout } = useAuth();
  const [showForceSyncDialog, setShowForceSyncDialog] = useState(false);

  const displayName = user?.displayName ?? user?.email ?? "";
  const initials = user?.displayName
    ? user.displayName
        .split(" ")
        .map((part) => part[0])
        .join("")
        .toUpperCase()
    : (user?.email?.[0] ?? "").toUpperCase();

  return (
    <SidebarFooter>
      <SidebarMenu>
        <SidebarMenuItem>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <SidebarMenuButton
                size="lg"
                className="data-[state=open]:bg-sidebar-accent data-[state=open]:text-sidebar-accent-foreground"
              >
                <Avatar className="size-8 rounded-lg">
                  <AvatarImage src={user?.avatarUrl ?? undefined} alt={displayName} />
                  <AvatarFallback className="rounded-lg text-xs">{initials}</AvatarFallback>
                </Avatar>
                <div className="grid flex-1 text-left text-sm leading-tight">
                  <span className="truncate font-semibold">{displayName}</span>
                  <span className="text-muted-foreground truncate text-xs">{user?.email}</span>
                </div>
              </SidebarMenuButton>
            </DropdownMenuTrigger>
            <DropdownMenuContent
              className="w-[--radix-dropdown-menu-trigger-width] min-w-56"
              side="top"
              align="start"
              sideOffset={4}
            >
              <DropdownMenuItem
                onClick={() => setTheme(resolvedTheme === "dark" ? "light" : "dark")}
              >
                {resolvedTheme === "dark" ? (
                  <Sun className="mr-2 size-4" />
                ) : (
                  <Moon className="mr-2 size-4" />
                )}
                {resolvedTheme === "dark" ? "Light mode" : "Dark mode"}
              </DropdownMenuItem>
              {onForceSync && (
                <DropdownMenuItem onClick={() => setShowForceSyncDialog(true)}>
                  <RefreshCw className="mr-2 size-4" />
                  Force full sync
                </DropdownMenuItem>
              )}
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={logout}>
                <LogOut className="mr-2 size-4" />
                Log out
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </SidebarMenuItem>
      </SidebarMenu>

      <Dialog open={showForceSyncDialog} onOpenChange={setShowForceSyncDialog}>
        <DialogContent showCloseButton={false} className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Force full sync?</DialogTitle>
            <DialogDescription>
              This will rescan all Drive folders from scratch. It may take several minutes for large
              folder trees.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowForceSyncDialog(false)}>
              Cancel
            </Button>
            <Button
              onClick={() => {
                setShowForceSyncDialog(false);
                onForceSync?.();
              }}
            >
              <RefreshCw className="mr-2 size-4" />
              Force sync
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </SidebarFooter>
  );
}

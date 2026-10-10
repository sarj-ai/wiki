"use client";

import { useQueryClient } from "@tanstack/react-query";
import { ChevronsUpDown, LogOut, Moon, Settings, Sun } from "lucide-react";
import dynamic from "next/dynamic";
import { useTheme } from "next-themes";

import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { SidebarFooter } from "@/components/ui/sidebar";
import { useCurrentUser } from "@/hooks/use-current-user";
import { useDeferredDialog } from "@/hooks/use-deferred-dialog";
import { useIsClient } from "@/hooks/use-is-client";
import { logout as apiLogout } from "@/lib/auth";
import { broadcastSessionClear, clearAuthenticatedSession } from "@/lib/session-isolation";

const PreferencesDialog = dynamic(
  async () => {
    const loadedModule = await import("./preferences-dialog");
    return loadedModule.PreferencesDialog;
  },
  { ssr: false },
);

export function WikiFooter() {
  const user = useCurrentUser().data ?? null;
  const queryClient = useQueryClient();
  const logout = async () => {
    const userId = user?.id ?? null;
    clearAuthenticatedSession(queryClient, userId);
    broadcastSessionClear();
    await apiLogout();
    window.location.replace("/login");
  };
  const { resolvedTheme, setTheme } = useTheme();
  const mounted = useIsClient();

  const preferencesDialog = useDeferredDialog();

  const isDark = mounted && resolvedTheme === "dark";
  const toggleTheme = () => {
    setTheme(isDark ? "light" : "dark");
  };

  const initials = (user?.displayName ?? user?.email ?? "?")
    .split(/[\s@.]+/)
    .filter(Boolean)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .slice(0, 2)
    .join("");

  return (
    <SidebarFooter className="border-sidebar-border border-t p-1">
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            aria-label="Account menu"
            className="hover:bg-sidebar-accent hover:text-sidebar-accent-foreground h-auto w-full justify-start px-2 py-1.5 font-normal"
            variant="ghost"
          >
            <Avatar className="bg-sidebar-accent text-sidebar-accent-foreground">
              {user?.avatarUrl ? (
                <AvatarImage
                  alt={user.displayName ?? user.email}
                  className="object-cover"
                  referrerPolicy="no-referrer"
                  src={user.avatarUrl}
                />
              ) : null}
              <AvatarFallback className="bg-sidebar-accent text-sidebar-accent-foreground text-xs font-semibold">
                {initials || "?"}
              </AvatarFallback>
            </Avatar>
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium" title={user?.displayName ?? ""}>
                {user?.displayName ?? "Signed in"}
              </div>
              <div className="text-muted-foreground truncate text-xs" title={user?.email ?? ""}>
                {user?.email ?? ""}
              </div>
            </div>
            <ChevronsUpDown className="text-muted-foreground size-4 shrink-0" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-56" side="top">
          <DropdownMenuItem
            className="flex items-center gap-2 text-sm"
            onSelect={(e) => {
              e.preventDefault();
              preferencesDialog.onOpenChange(true);
            }}
          >
            <Settings className="size-4" />
            Preferences
          </DropdownMenuItem>
          <DropdownMenuItem
            className="flex items-center gap-2 text-sm"
            onSelect={(e) => {
              e.preventDefault();
              toggleTheme();
            }}
          >
            {mounted ? isDark ? <Sun className="size-4" /> : <Moon className="size-4" /> : null}
            {isDark ? "Switch to light mode" : "Switch to dark mode"}
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            className="text-destructive focus:text-destructive flex items-center gap-2 text-sm"
            onSelect={() => {
              void logout();
            }}
          >
            <LogOut className="size-4" />
            Log out
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      {preferencesDialog.mounted ? (
        <PreferencesDialog
          onOpenChange={preferencesDialog.onOpenChange}
          open={preferencesDialog.open}
        />
      ) : null}
    </SidebarFooter>
  );
}

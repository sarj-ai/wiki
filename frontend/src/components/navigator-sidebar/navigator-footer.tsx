"use client";

import { ChevronsUpDown, LogOut, Moon, Settings, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import { useState } from "react";

import { useAuth } from "@/components/auth-provider";
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
import { useIsClient } from "@/hooks/use-is-client";

import { PreferencesDialog } from "./preferences-dialog";

export function NavigatorFooter() {
  const { user, logout } = useAuth();
  const { resolvedTheme, setTheme } = useTheme();
  const mounted = useIsClient();
  // Preferences dialog state is owned by the footer so the dropdown
  // can close while the dialog stays open — the two radix popovers
  // don't nest cleanly otherwise.
  const [prefsOpen, setPrefsOpen] = useState(false);

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
            variant="ghost"
            className="hover:bg-sidebar-accent hover:text-sidebar-accent-foreground h-auto w-full justify-start px-2 py-1.5 font-normal"
            aria-label="Account menu"
          >
            <Avatar className="bg-sidebar-accent text-sidebar-accent-foreground">
              {user?.avatarUrl && (
                // lh3.googleusercontent.com 403s on requests that send
                // a cross-origin Referer header — referrerPolicy
                // strips it so the avatar actually loads.
                <AvatarImage
                  src={user.avatarUrl}
                  alt={user.displayName ?? user.email}
                  referrerPolicy="no-referrer"
                  className="object-cover"
                />
              )}
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
        <DropdownMenuContent align="end" side="top" className="w-56">
          <DropdownMenuItem
            onSelect={(e) => {
              e.preventDefault();
              setPrefsOpen(true);
            }}
            className="flex items-center gap-2 text-sm"
          >
            <Settings className="size-4" />
            Preferences
          </DropdownMenuItem>
          <DropdownMenuItem
            onSelect={(e) => {
              e.preventDefault();
              toggleTheme();
            }}
            className="flex items-center gap-2 text-sm"
          >
            {mounted && (isDark ? <Sun className="size-4" /> : <Moon className="size-4" />)}
            {isDark ? "Switch to light mode" : "Switch to dark mode"}
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            onSelect={() => {
              void logout();
            }}
            className="text-destructive focus:text-destructive flex items-center gap-2 text-sm"
          >
            <LogOut className="size-4" />
            Log out
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <PreferencesDialog open={prefsOpen} onOpenChange={setPrefsOpen} />
    </SidebarFooter>
  );
}

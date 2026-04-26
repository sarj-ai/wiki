"use client";

import { ChevronsUpDown, LogOut, Moon, Settings, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import { useState } from "react";

import { useAuth } from "@/components/auth-provider";
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
  const toggleTheme = () => { setTheme(isDark ? "light" : "dark"); };

  const initials = (user?.displayName ?? user?.email ?? "?")
    .split(/[\s@.]+/)
    .filter(Boolean)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .slice(0, 2)
    .join("");

  const hasAvatar = Boolean(user?.avatarUrl);

  return (
    <SidebarFooter className="border-sidebar-border border-t p-1">
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            className="hover:bg-sidebar-accent hover:text-sidebar-accent-foreground flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left outline-none"
            aria-label="Account menu"
          >
            <div className="bg-sidebar-accent text-sidebar-accent-foreground flex size-8 shrink-0 items-center justify-center overflow-hidden rounded-full text-xs font-semibold">
              {hasAvatar && user?.avatarUrl ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={user.avatarUrl}
                  alt={user.displayName ?? user.email}
                  // lh3.googleusercontent.com 403s on requests that send
                  // a cross-origin Referer header. next/image can't
                  // strip referrers, so we use a plain <img> tag with
                  // referrerPolicy="no-referrer".
                  referrerPolicy="no-referrer"
                  className="size-full object-cover"
                />
              ) : (
                initials || "?"
              )}
            </div>
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium" title={user?.displayName ?? ""}>
                {user?.displayName ?? "Signed in"}
              </div>
              <div className="text-muted-foreground truncate text-xs" title={user?.email ?? ""}>
                {user?.email ?? ""}
              </div>
            </div>
            <ChevronsUpDown className="text-muted-foreground size-4 shrink-0" />
          </button>
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

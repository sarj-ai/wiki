"use client";

import { LogOut, Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import { useSyncExternalStore } from "react";
import { SidebarFooter } from "@/components/ui/sidebar";
import { useAuth } from "@/components/auth-provider";

// Detects client-side render without triggering set-state-in-effect.
// Returns false during SSR so the theme icon doesn't flicker between
// server HTML and post-hydration state.
const subscribe = () => () => {};
function useIsClient(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => true,
    () => false,
  );
}

export function NavigatorFooter() {
  const { user, logout } = useAuth();
  const { resolvedTheme, setTheme } = useTheme();
  const mounted = useIsClient();

  const isDark = mounted && resolvedTheme === "dark";
  const toggleTheme = () => setTheme(isDark ? "light" : "dark");

  const initials = (user?.displayName ?? user?.email ?? "?")
    .split(/[\s@.]+/)
    .filter(Boolean)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .slice(0, 2)
    .join("");

  return (
    <SidebarFooter className="border-sidebar-border border-t">
      <div className="flex items-center gap-2 px-2 py-2">
        <div className="bg-sidebar-accent text-sidebar-accent-foreground flex size-8 shrink-0 items-center justify-center rounded-full text-xs font-semibold">
          {initials || "?"}
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium" title={user?.displayName ?? ""}>
            {user?.displayName ?? "Signed in"}
          </div>
          <div className="text-muted-foreground truncate text-xs" title={user?.email ?? ""}>
            {user?.email ?? ""}
          </div>
        </div>
        <button
          type="button"
          onClick={toggleTheme}
          title={isDark ? "Switch to light mode" : "Switch to dark mode"}
          className="text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground flex size-7 shrink-0 items-center justify-center rounded-md outline-none"
          aria-label="Toggle theme"
        >
          {mounted && (isDark ? <Sun className="size-4" /> : <Moon className="size-4" />)}
        </button>
        <button
          type="button"
          onClick={() => {
            void logout();
          }}
          title="Log out"
          className="text-sidebar-foreground hover:bg-destructive hover:text-destructive-foreground flex size-7 shrink-0 items-center justify-center rounded-md outline-none"
          aria-label="Log out"
        >
          <LogOut className="size-4" />
        </button>
      </div>
    </SidebarFooter>
  );
}

import { cookies } from "next/headers";

import { ErrorBoundary } from "@/components/error-boundary";
import { SidebarProvider } from "@/components/ui/sidebar";
import { CommandPalette } from "@/components/wiki-search/command-palette";
import { WikiSidebar } from "@/components/wiki-sidebar/wiki-sidebar";

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const cookieStore = await cookies();
  const sidebarState = cookieStore.get("sidebar_state")?.value;
  const defaultOpen = sidebarState !== undefined ? sidebarState === "true" : true;

  return (
    <SidebarProvider defaultOpen={defaultOpen}>
      <ErrorBoundary>
        <WikiSidebar />
      </ErrorBoundary>
      <main className="flex-1 overflow-auto">
        <ErrorBoundary>{children}</ErrorBoundary>
      </main>
      {/* Mounted once per authenticated session. Registers the global
          Cmd+K hotkey and renders the Spotlight-style palette on demand. */}
      <CommandPalette />
    </SidebarProvider>
  );
}

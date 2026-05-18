import { cookies } from "next/headers";

import { ErrorBoundary } from "@/components/error-boundary";
import { SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar";
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
        {/* Mobile-only sidebar trigger. On desktop the sidebar is
            permanently visible to the left of ``main`` so the trigger
            would be redundant; on phones (< 768px) ``Sidebar`` renders
            as a slide-out ``Sheet`` and this is the only affordance
            for opening it. Floats at top-left so it stays reachable
            on every screen — home page, file view, 404, etc. */}
        <SidebarTrigger className="bg-background/80 fixed top-3 left-3 z-50 backdrop-blur md:hidden" />
        <ErrorBoundary>{children}</ErrorBoundary>
      </main>
      {/* Mounted once per authenticated session. Registers the global
          Cmd+K hotkey and renders the Spotlight-style palette on demand. */}
      <CommandPalette />
    </SidebarProvider>
  );
}

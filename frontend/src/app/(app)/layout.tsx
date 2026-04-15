import { cookies } from "next/headers";
import { SidebarProvider } from "@/components/ui/sidebar";
import { NavigatorSidebar } from "@/components/navigator-sidebar/navigator-sidebar";
import { CommandPalette } from "@/components/navigator-search/command-palette";
import { ErrorBoundary } from "@/components/error-boundary";

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const cookieStore = await cookies();
  const sidebarState = cookieStore.get("sidebar_state")?.value;
  const defaultOpen = sidebarState !== undefined ? sidebarState === "true" : true;

  return (
    <SidebarProvider defaultOpen={defaultOpen}>
      <ErrorBoundary>
        <NavigatorSidebar />
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

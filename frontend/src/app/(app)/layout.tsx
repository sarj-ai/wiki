import { dehydrate, HydrationBoundary, QueryClient } from "@tanstack/react-query";
import { cookies } from "next/headers";

import { ErrorBoundary } from "@/components/error-boundary";
import { RedirectAfterLogin } from "@/components/redirect-after-login";
import { SessionRefresh } from "@/components/session-refresh";
import { SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar";
import { DocViewerHost } from "@/components/wiki-document/doc-viewer-host";
import { CommandPalette } from "@/components/wiki-search/command-palette";
import { WikiSidebar } from "@/components/wiki-sidebar/wiki-sidebar";
import { serverWikiApi } from "@/lib/server-api";
import { ZSidebarStateCookie } from "@/lib/storage-schemas";
import { authKeys, wikiKeys } from "@/lib/wiki-keys";

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const cookieStore = await cookies();
  const defaultOpen = ZSidebarStateCookie.parse(cookieStore.get("sidebar_state")?.value);

  // Fresh per-request client — module-level would leak between users.
  const queryClient = new QueryClient();
  const bootstrap = await serverWikiApi.getBootstrap().catch(() => null);
  if (bootstrap) {
    queryClient.setQueryData(wikiKeys.drives, bootstrap.drives);
    queryClient.setQueryData(wikiKeys.preferences, bootstrap.preferences);
    queryClient.setQueryData(wikiKeys.folderIcons, bootstrap.folderIcons);
    queryClient.setQueryData(wikiKeys.docIcons, bootstrap.docIcons);
    queryClient.setQueryData(wikiKeys.sortOrders, bootstrap.sortOrders);
    queryClient.setQueryData(authKeys.me, bootstrap.me);
  }

  return (
    <HydrationBoundary state={dehydrate(queryClient)}>
      <SidebarProvider defaultOpen={defaultOpen}>
        <RedirectAfterLogin />
        <SessionRefresh />
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
            Cmd+K hotkey and renders the Spotlight-style palette on demand.
            Has its own boundary so a palette render error degrades to a
            broken palette, not a full-page global-error takeover. */}
        <ErrorBoundary>
          <CommandPalette />
        </ErrorBoundary>
        <ErrorBoundary>
          <DocViewerHost />
        </ErrorBoundary>
      </SidebarProvider>
    </HydrationBoundary>
  );
}

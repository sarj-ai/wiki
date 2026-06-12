import { dehydrate, HydrationBoundary, QueryClient } from "@tanstack/react-query";
import { cookies } from "next/headers";

import { ErrorBoundary } from "@/components/error-boundary";
import { RedirectAfterLogin } from "@/components/redirect-after-login";
import { SessionRefresh } from "@/components/session-refresh";
import { SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar";
import { CommandPalette } from "@/components/wiki-search/command-palette";
import { WikiSidebar } from "@/components/wiki-sidebar/wiki-sidebar";
import { serverWikiApi } from "@/lib/server-api";
import { authKeys, wikiKeys } from "@/lib/wiki-keys";

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const cookieStore = await cookies();
  const sidebarState = cookieStore.get("sidebar_state")?.value;
  const defaultOpen = sidebarState !== undefined ? sidebarState === "true" : true;

  // Fresh per-request client — module-level would leak between users.
  // One aggregate ``/bootstrap`` round-trip replaces the six
  // per-resource prefetches (each Worker→origin call costs ~600ms of
  // RTT floor); the payload is fanned back out under the same query
  // keys the client hooks read, so hydration is unchanged. Errors are
  // swallowed like prefetchQuery's were — backend-down degrades to the
  // client-side flow (the 401-refresh interceptor + retries). Drive
  // trees deliberately not prefetched (heavy; dnd-kit's counter-based
  // aria ids would SSR-mismatch).
  const queryClient = new QueryClient();
  try {
    const bootstrap = await serverWikiApi.getBootstrap();
    queryClient.setQueryData(wikiKeys.drives, bootstrap.drives);
    queryClient.setQueryData(wikiKeys.preferences, bootstrap.preferences);
    queryClient.setQueryData(wikiKeys.folderIcons, bootstrap.folderIcons);
    queryClient.setQueryData(wikiKeys.docIcons, bootstrap.docIcons);
    queryClient.setQueryData(wikiKeys.sortOrders, bootstrap.sortOrders);
    queryClient.setQueryData(authKeys.me, bootstrap.me);
  } catch {
    // Render the shell anyway; every consumer hook refetches on mount.
  }

  return (
    <HydrationBoundary state={dehydrate(queryClient)}>
      <SidebarProvider defaultOpen={defaultOpen}>
        <RedirectAfterLogin />
        {/* Keeps auth cookies rotated ahead of expiry — see component docs. */}
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
      </SidebarProvider>
    </HydrationBoundary>
  );
}

import { dehydrate, HydrationBoundary, QueryClient } from "@tanstack/react-query";
import { cookies } from "next/headers";
import { z } from "zod";

import { ErrorBoundary } from "@/components/error-boundary";
import { RedirectAfterLogin } from "@/components/redirect-after-login";
import { SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar";
import { CommandPalette } from "@/components/wiki-search/command-palette";
import { WikiSidebar } from "@/components/wiki-sidebar/wiki-sidebar";
import {
  zDocIconRead,
  zDriveSummary,
  zFolderIconRead,
  zPreferencesRead,
  zSortOrderRead,
  zUserProfile,
} from "@/lib/generated/zod.gen";
import { serverGet } from "@/lib/server-api";
import { authKeys, wikiKeys } from "@/lib/wiki-keys";

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const cookieStore = await cookies();
  const sidebarState = cookieStore.get("sidebar_state")?.value;
  const defaultOpen = sidebarState !== undefined ? sidebarState === "true" : true;

  // Fresh per-request client — module-level would leak between users.
  // prefetchQuery swallows errors, so backend-down degrades to the
  // client-side flow. Drive trees deliberately not prefetched (heavy;
  // dnd-kit's counter-based aria ids would SSR-mismatch).
  const queryClient = new QueryClient();
  await Promise.all([
    queryClient.prefetchQuery({
      queryKey: wikiKeys.drives,
      queryFn: () => serverGet("sidebar/drives", z.array(zDriveSummary)),
    }),
    queryClient.prefetchQuery({
      queryKey: wikiKeys.preferences,
      queryFn: () => serverGet("extras/preferences", zPreferencesRead),
    }),
    queryClient.prefetchQuery({
      queryKey: wikiKeys.folderIcons,
      queryFn: () => serverGet("extras/folder-icons", z.array(zFolderIconRead)),
    }),
    queryClient.prefetchQuery({
      queryKey: wikiKeys.docIcons,
      queryFn: () => serverGet("extras/doc-icons", z.array(zDocIconRead)),
    }),
    queryClient.prefetchQuery({
      queryKey: wikiKeys.sortOrders,
      queryFn: () => serverGet("extras/sort-order", z.array(zSortOrderRead)),
    }),
    queryClient.prefetchQuery({
      queryKey: authKeys.me,
      queryFn: () => serverGet("auth/me", zUserProfile),
    }),
  ]);

  return (
    <HydrationBoundary state={dehydrate(queryClient)}>
      <SidebarProvider defaultOpen={defaultOpen}>
        <RedirectAfterLogin />
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

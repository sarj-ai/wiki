import { dehydrate, HydrationBoundary, QueryClient } from "@tanstack/react-query";
import { cookies } from "next/headers";

import { ErrorBoundary } from "@/components/error-boundary";
import { RedirectAfterLogin } from "@/components/redirect-after-login";
import { SessionRefresh } from "@/components/session-refresh";
import { SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar";
import { DocSiblingWarmer } from "@/components/wiki-document/doc-sibling-warmer";
import { DocViewerHost } from "@/components/wiki-document/doc-viewer-host";
import { DocWarmup } from "@/components/wiki-document/doc-warmup";
import { CommandPaletteMount } from "@/components/wiki-search/command-palette-mount";
import { WikiSidebar } from "@/components/wiki-sidebar/wiki-sidebar";
import { serverWikiApi } from "@/lib/server-api";
import { ZSidebarStateCookie } from "@/lib/storage-schemas";
import { authKeys, wikiKeys } from "@/lib/wiki-keys";

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const cookieStore = await cookies();
  const defaultOpen = ZSidebarStateCookie.parse(cookieStore.get("sidebar_state")?.value);

  const queryClient = new QueryClient();
  const bootstrap = await serverWikiApi.getBootstrap().catch(() => null);
  if (bootstrap) {
    queryClient.setQueryData(wikiKeys.drives, bootstrap.drives);
    queryClient.setQueryData(wikiKeys.preferences, bootstrap.preferences);
    queryClient.setQueryData(wikiKeys.folderIcons, bootstrap.folderIcons);
    queryClient.setQueryData(wikiKeys.docIcons, bootstrap.docIcons);
    queryClient.setQueryData(wikiKeys.sortOrders, bootstrap.sortOrders);
    queryClient.setQueryData(authKeys.me, bootstrap.me);
    if (bootstrap.firstDriveTree) {
      queryClient.setQueryData(
        wikiKeys.driveTree(bootstrap.firstDriveTree.driveId),
        bootstrap.firstDriveTree,
      );
    }
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
          <SidebarTrigger className="bg-background/80 fixed top-3 left-3 z-50 backdrop-blur md:hidden" />
          <ErrorBoundary>{children}</ErrorBoundary>
        </main>

        <ErrorBoundary>
          <CommandPaletteMount />
        </ErrorBoundary>
        <ErrorBoundary>
          <DocViewerHost />
        </ErrorBoundary>
        <DocWarmup />
        <DocSiblingWarmer />
      </SidebarProvider>
    </HydrationBoundary>
  );
}

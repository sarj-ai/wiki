import { dehydrate, HydrationBoundary, QueryClient } from "@tanstack/react-query";
import { cookies } from "next/headers";
import { type CSSProperties, Suspense } from "react";

import { BrowserSessionBoundary } from "@/components/browser-session-boundary";
import { ErrorBoundary } from "@/components/error-boundary";
import { QueryProvider } from "@/components/query-provider";
import { RedirectAfterLogin } from "@/components/redirect-after-login";
import { SarjLoader } from "@/components/sarj-loader";
import { SessionRefresh } from "@/components/session-refresh";
import { SidebarProvider } from "@/components/ui/sidebar";
import { Toaster } from "@/components/ui/sonner";
import { DocViewerHost } from "@/components/wiki-document/doc-viewer-host";
import { DocWarmup } from "@/components/wiki-document/doc-warmup";
import { CommandPaletteMount } from "@/components/wiki-search/command-palette-mount";
import { WikiSidebar } from "@/components/wiki-sidebar/wiki-sidebar";
import { WorkspaceFrame } from "@/components/workspace-frame";
import { loadSessionBootstrap } from "@/lib/load-session-bootstrap";
import { serverWikiApi } from "@/lib/server-api";
import {
  SIDEBAR_WIDTH_COOKIE,
  SidebarStateCookieSchema,
  SidebarWidthCssSchema,
} from "@/lib/storage-schemas";
import { authKeys, wikiKeys } from "@/lib/wiki-keys";

interface SidebarStyle extends CSSProperties {
  "--sidebar-width"?: string;
}

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <Suspense fallback={<WorkspaceSkeleton />}>
      <AuthenticatedLayout>{children}</AuthenticatedLayout>
    </Suspense>
  );
}

function WorkspaceSkeleton() {
  return (
    <main aria-busy="true" className="bg-background flex min-h-svh items-center justify-center p-6">
      <SarjLoader label="Loading workspace…" />
    </main>
  );
}

async function AuthenticatedLayout({ children }: { children: React.ReactNode }) {
  const cookieStore = await cookies();
  const defaultOpen = SidebarStateCookieSchema.parse(cookieStore.get("sidebar_state")?.value);
  const savedWidth = SidebarWidthCssSchema.parse(cookieStore.get(SIDEBAR_WIDTH_COOKIE)?.value);
  const sidebarWidth = savedWidth
    ? `${Math.min(576, Math.max(224, Number.parseInt(savedWidth, 10)))}px`
    : undefined;
  const sidebarStyle: SidebarStyle | undefined =
    sidebarWidth === undefined ? undefined : { "--sidebar-width": sidebarWidth };

  const queryClient = new QueryClient();
  const { bootstrap, me } = await loadSessionBootstrap(serverWikiApi);
  if (me) queryClient.setQueryData(authKeys.me, me);
  if (bootstrap) {
    queryClient.setQueryData(wikiKeys.drives, bootstrap.drives);
    queryClient.setQueryData(wikiKeys.preferences, bootstrap.preferences);
    queryClient.setQueryData(wikiKeys.folderIcons, bootstrap.folderIcons);
    // An empty list means the render-blocking bootstrap intentionally deferred
    // provider-authorized icon metadata to the dedicated client query.
    if (bootstrap.docIcons.length > 0) {
      queryClient.setQueryData(wikiKeys.docIcons, bootstrap.docIcons);
    }
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
    <QueryProvider key={me?.id ?? "anonymous"} userId={me?.id ?? null}>
      <HydrationBoundary state={dehydrate(queryClient)}>
        <BrowserSessionBoundary initialUserId={me?.id ?? null}>
          <SidebarProvider defaultOpen={defaultOpen} style={sidebarStyle}>
            <RedirectAfterLogin />
            <SessionRefresh />
            <ErrorBoundary>
              <WikiSidebar />
            </ErrorBoundary>
            <WorkspaceFrame>
              <ErrorBoundary>{children}</ErrorBoundary>
            </WorkspaceFrame>

            <ErrorBoundary>
              <CommandPaletteMount />
            </ErrorBoundary>
            <ErrorBoundary>
              <DocViewerHost />
            </ErrorBoundary>
            <DocWarmup />
            <Toaster position="bottom-right" />
          </SidebarProvider>
        </BrowserSessionBoundary>
      </HydrationBoundary>
    </QueryProvider>
  );
}

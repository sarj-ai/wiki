"use client";

import { type QueryClient, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, useSyncExternalStore } from "react";

import { RecentPagesSessionProvider } from "@/components/recent-pages/recent-pages-session";
import { currentUserQueryOptions, useCurrentUser } from "@/hooks/use-current-user";
import { resetExpansionState } from "@/hooks/use-expanded-folders";
import {
  clearUserBrowserStorage,
  purgeLegacyBrowserStorage,
  readActiveBrowserUser,
  writeActiveBrowserUser,
} from "@/lib/browser-storage";
import { BrowserUserContext } from "@/lib/browser-user-context";
import { captureMutationSession } from "@/lib/mutation-session";
import {
  broadcastSessionClear,
  resetDocumentViewer,
  resetSidebarState,
} from "@/lib/session-isolation";
import { claimVerifiedBrowserUser, readVerifiedBrowserUser } from "@/lib/verified-browser-user";

export function BrowserSessionBoundary({
  children,
  initialUserId,
}: {
  children: React.ReactNode;
  initialUserId: null | string;
}) {
  if (initialUserId === null) {
    return <RecoverBrowserSessionBoundary>{children}</RecoverBrowserSessionBoundary>;
  }
  return (
    <IdentifiedBrowserSessionBoundary currentUserId={initialUserId}>
      {children}
    </IdentifiedBrowserSessionBoundary>
  );
}

function RecoverBrowserSessionBoundary({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient();
  const [currentUserId, setCurrentUserId] = useState<null | string>(null);
  const profile = useCurrentUser();
  const sessionActive = useSessionActive(queryClient);

  useEffect(() => {
    const lifetime = new AbortController();
    const session = captureMutationSession(queryClient);
    void (async () => {
      // A fresh read may share the account menu's in-flight query. Cached
      // profiles alone cannot establish a browser persistence namespace.
      const user = await queryClient.query({ ...currentUserQueryOptions(), staleTime: 0 });
      if (lifetime.signal.aborted || !claimVerifiedBrowserUser(queryClient, user.id, session))
        return;
      setCurrentUserId(user.id);
    })().catch(() => {
      if (!lifetime.signal.aborted) setCurrentUserId(null);
    });
    return () => {
      // The QueryProvider owns query cancellation; other observers may need it.
      lifetime.abort();
    };
  }, [queryClient]);

  const activeUserId =
    sessionActive &&
    profile.isSuccess &&
    profile.data.id === currentUserId &&
    readVerifiedBrowserUser(queryClient) === currentUserId
      ? currentUserId
      : null;
  return (
    <IdentifiedBrowserSessionBoundary currentUserId={activeUserId}>
      {children}
    </IdentifiedBrowserSessionBoundary>
  );
}

function useSessionActive(queryClient: QueryClient): boolean {
  return useSyncExternalStore(
    (onChange) => {
      const session = captureMutationSession(queryClient);
      session.addEventListener("abort", onChange);
      return () => session.removeEventListener("abort", onChange);
    },
    () => !captureMutationSession(queryClient).aborted,
    noServerSession,
  );
}

function noServerSession(): boolean {
  return false;
}

function IdentifiedBrowserSessionBoundary({
  children,
  currentUserId,
}: {
  children: React.ReactNode;
  currentUserId: null | string;
}) {
  useEffect(() => {
    purgeLegacyBrowserStorage();
    if (!currentUserId) return;
    const previousUserId = readActiveBrowserUser();
    if (previousUserId && previousUserId !== currentUserId) {
      resetExpansionState(previousUserId);
      clearUserBrowserStorage(previousUserId);
      resetDocumentViewer();
      resetSidebarState();
      broadcastSessionClear();
    }
    writeActiveBrowserUser(currentUserId);
  }, [currentUserId]);

  return (
    <BrowserUserContext value={currentUserId}>
      <RecentPagesSessionProvider userId={currentUserId}>{children}</RecentPagesSessionProvider>
    </BrowserUserContext>
  );
}

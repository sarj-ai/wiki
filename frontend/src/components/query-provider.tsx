"use client";

import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useLayoutEffect, useRef, useState } from "react";

import { ApiError } from "@/lib/api-error";
import { claimBrowserSession, retireBrowserSession } from "@/lib/browser-session";
import { storeLoginRedirect } from "@/lib/browser-storage";
import {
  activateMutationSession,
  captureMutationSession,
  retireMutationSession,
} from "@/lib/mutation-session";
import { sanitizeRedirectPath } from "@/lib/sanitize-redirect-path";
import { notifyTerminalUnauthorized, TERMINAL_UNAUTHORIZED_EVENT } from "@/lib/session-events";
import {
  broadcastSessionClear,
  clearAuthenticatedSession,
  subscribeToSessionClear,
} from "@/lib/session-isolation";
import { readVerifiedBrowserUser } from "@/lib/verified-browser-user";

const ONE_DAY_MS = 24 * 60 * 60 * 1000;

export function QueryProvider({
  children,
  userId,
}: {
  children: React.ReactNode;
  userId: null | string;
}) {
  const redirecting = useRef(false);

  const [{ activate, queryClient, retire }] = useState(() => {
    const ownership = { active: true };
    const handle401 = (err: unknown): void => {
      if (
        ownership.active &&
        !captureMutationSession(client).aborted &&
        err instanceof ApiError &&
        err.status === 401
      ) {
        notifyTerminalUnauthorized();
      }
    };
    const client = new QueryClient({
      defaultOptions: {
        queries: {
          gcTime: ONE_DAY_MS,
          refetchOnMount: false,

          retry: (failureCount, error) => {
            if (failureCount >= 1) return false;
            if (error instanceof ApiError) return error.status >= 500;
            return true;
          },
          staleTime: 60 * 1000,
        },
      },
      mutationCache: new MutationCache({ onError: handle401 }),
      queryCache: new QueryCache({ onError: handle401 }),
    });
    return {
      activate: () => {
        ownership.active = true;
      },
      queryClient: client,
      retire: () => {
        ownership.active = false;
        // Abort reads before a replacement provider can observe their terminal
        // auth events. Strict Mode immediately restores this owner's effect,
        // so defer clearing its cache until cancellation has finished.
        void (async () => {
          await client.cancelQueries();
          if (!ownership.active) client.clear();
        })().catch(() => null);
      },
    };
  });

  useLayoutEffect(() => {
    // Restore the epoch before child passive effects replay in Strict Mode.
    activateMutationSession(queryClient);
    return () => retireMutationSession(queryClient);
  }, [queryClient]);

  useEffect(() => {
    const owner = {};
    claimBrowserSession(owner);
    activate();
    const handleTerminalUnauthorized = () => {
      if (redirecting.current || window.location.pathname === "/login") return;
      redirecting.current = true;
      const safePath = sanitizeRedirectPath(window.location.pathname);
      clearAuthenticatedSession(queryClient, userId ?? readVerifiedBrowserUser(queryClient));
      broadcastSessionClear();
      storeLoginRedirect(safePath);
      window.location.replace(`/login?redirect=${encodeURIComponent(safePath)}`);
    };
    window.addEventListener(TERMINAL_UNAUTHORIZED_EVENT, handleTerminalUnauthorized);
    const unsubscribe = subscribeToSessionClear(() => {
      if (redirecting.current) return;
      redirecting.current = true;
      clearAuthenticatedSession(queryClient, userId ?? readVerifiedBrowserUser(queryClient));
      window.location.replace("/login");
    });
    return () => {
      retireBrowserSession(owner);
      window.removeEventListener(TERMINAL_UNAUTHORIZED_EVENT, handleTerminalUnauthorized);
      unsubscribe();
      retire();
    };
  }, [activate, queryClient, retire, userId]);

  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

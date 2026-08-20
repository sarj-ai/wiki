"use client";

import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { ApiError } from "@/lib/api-error";
import { purgeLegacyBrowserStorage, storeLoginRedirect } from "@/lib/browser-storage";
import { notifyTerminalUnauthorized, TERMINAL_UNAUTHORIZED_EVENT } from "@/lib/session-events";
import {
  broadcastSessionClear,
  clearAuthenticatedSession,
  subscribeToSessionClear,
} from "@/lib/session-isolation";
import { sanitizeRedirectPath } from "@/lib/url-utils";

const ONE_DAY_MS = 24 * 60 * 60 * 1000;

export function QueryProvider({
  children,
  userId,
}: {
  children: React.ReactNode;
  userId: null | string;
}) {
  const redirecting = useRef(false);

  const [queryClient] = useState(() => {
    if (typeof window !== "undefined") purgeLegacyBrowserStorage();
    const handle401 = (err: unknown): void => {
      if (err instanceof ApiError && err.status === 401) notifyTerminalUnauthorized();
    };
    return new QueryClient({
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
  });

  useEffect(() => {
    const handleTerminalUnauthorized = () => {
      if (redirecting.current || window.location.pathname === "/login") return;
      redirecting.current = true;
      const safePath = sanitizeRedirectPath(window.location.pathname);
      clearAuthenticatedSession(queryClient, userId);
      broadcastSessionClear();
      storeLoginRedirect(safePath);
      window.location.replace(`/login?redirect=${encodeURIComponent(safePath)}`);
    };
    window.addEventListener(TERMINAL_UNAUTHORIZED_EVENT, handleTerminalUnauthorized);
    const unsubscribe = subscribeToSessionClear(() => {
      if (redirecting.current) return;
      redirecting.current = true;
      clearAuthenticatedSession(queryClient, userId);
      window.location.replace("/login");
    });
    return () => {
      window.removeEventListener(TERMINAL_UNAUTHORIZED_EVENT, handleTerminalUnauthorized);
      unsubscribe();
    };
  }, [queryClient, userId]);

  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

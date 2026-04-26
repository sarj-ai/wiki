"use client";

import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { ApiError } from "@/lib/api-client";
import { sanitizeRedirectPath } from "@/lib/url-utils";

const REDIRECT_KEY = "sarj-redirect";

export function QueryProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  // Re-initialise the QueryClient if React's strict-mode double-mount
  // ever discards the first one — useState's lazy initialiser keeps
  // exactly one instance per provider mount.
  const [queryClient] = useState(() => {
    const handle401 = (err: unknown): void => {
      if (!(err instanceof ApiError) || err.status !== 401) return;
      if (typeof window === "undefined") return;
      if (window.location.pathname === "/login") return;
      const safePath = sanitizeRedirectPath(window.location.pathname);
      localStorage.setItem(REDIRECT_KEY, safePath);
      router.push(`/login?redirect=${encodeURIComponent(safePath)}`);
    };
    return new QueryClient({
      defaultOptions: {
        queries: { staleTime: 60 * 1000, retry: 1 },
      },
      queryCache: new QueryCache({ onError: handle401 }),
      mutationCache: new MutationCache({ onError: handle401 }),
    });
  });

  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

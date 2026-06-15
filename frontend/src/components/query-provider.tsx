"use client";

import { createAsyncStoragePersister } from "@tanstack/query-async-storage-persister";
import { MutationCache, QueryCache, QueryClient, type Query } from "@tanstack/react-query";
import { PersistQueryClientProvider } from "@tanstack/react-query-persist-client";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { ApiError } from "@/lib/api-client";
import { QUERY_CACHE_KEY, REDIRECT_KEY, sanitizeRedirectPath } from "@/lib/url-utils";

const CACHE_BUSTER = "v2";
const ONE_DAY_MS = 24 * 60 * 60 * 1000;

export function QueryProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();

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
        queries: {
          staleTime: 60 * 1000,
          refetchOnMount: false,

          gcTime: ONE_DAY_MS,
          retry: (failureCount, error) => {
            if (failureCount >= 1) return false;
            if (error instanceof ApiError) return error.status >= 500;
            return true;
          },
        },
      },
      queryCache: new QueryCache({ onError: handle401 }),
      mutationCache: new MutationCache({ onError: handle401 }),
    });
  });

  const [persistOptions] = useState(() => {
    const storage =
      typeof window === "undefined"
        ? {
            getItem: () => Promise.resolve(null),
            setItem: () => Promise.resolve(),
            removeItem: () => Promise.resolve(),
          }
        : window.localStorage;
    return {
      persister: createAsyncStoragePersister({
        storage,
        key: QUERY_CACHE_KEY,
      }),
      maxAge: ONE_DAY_MS,
      buster: CACHE_BUSTER,
      dehydrateOptions: {
        shouldDehydrateQuery: (query: Query) => {
          if (query.state.status !== "success") return false;
          const [root, scope] = query.queryKey;
          if (root !== "wiki") return false;
          if (scope === "drive-search") return false;
          return true;
        },
      },
    };
  });

  return (
    <PersistQueryClientProvider client={queryClient} persistOptions={persistOptions}>
      {children}
    </PersistQueryClientProvider>
  );
}

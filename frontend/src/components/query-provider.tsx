"use client";

import { createAsyncStoragePersister } from "@tanstack/query-async-storage-persister";
import { MutationCache, QueryCache, QueryClient, type Query } from "@tanstack/react-query";
import { PersistQueryClientProvider } from "@tanstack/react-query-persist-client";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { ApiError } from "@/lib/api-client";
import { cleanupLegacyLocalStorage } from "@/lib/legacy-localstorage-cleanup";
import { sanitizeRedirectPath } from "@/lib/url-utils";

const REDIRECT_KEY = "sarj-redirect";
// Bumped manually when persisted-cache shape would otherwise be
// incompatible with the running app — drops every cached query.
const CACHE_BUSTER = "v1";
const ONE_DAY_MS = 24 * 60 * 60 * 1000;

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
        queries: {
          staleTime: 60 * 1000,
          // gcTime must be ≥ the persister's maxAge — otherwise the
          // re-hydrated query would be GC'd before any consumer
          // mounts, defeating the persistence we just paid for.
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

  // Persister + PersistQueryClientProvider re-hydrates the cache from
  // localStorage on mount, then writes back on every cache change. The
  // sidebar paints from cache instantly on a hard reload, while a
  // background revalidation runs and replaces stale entries — the same
  // stale-while-revalidate pattern the server's `drive_tree_cache`
  // already uses, applied client-side. On the server the persister
  // gets a no-op storage so SSR / static rendering still resolves the
  // QueryClientProvider context (PersistQueryClientProvider supplies
  // it) without touching window.
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
        key: "sarj-wiki-query-cache",
      }),
      maxAge: ONE_DAY_MS,
      buster: CACHE_BUSTER,
      dehydrateOptions: {
        // Persist navigator state but skip ephemeral queries: search
        // results are debounced and per-query-string so the cache
        // would balloon, and stale snippets aren't useful anyway.
        // Errored queries are skipped too — re-hydrating an error
        // would surface a stale failure on the next mount.
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

  // One-shot sweep of legacy ``navigator-*`` localStorage keys left
  // behind by the W18 rename. Idempotent so subsequent loads no-op;
  // we can drop the call entirely after a few weeks once the cleanup
  // has propagated to typical users.
  useEffect(() => {
    cleanupLegacyLocalStorage();
  }, []);

  return (
    <PersistQueryClientProvider client={queryClient} persistOptions={persistOptions}>
      {children}
    </PersistQueryClientProvider>
  );
}

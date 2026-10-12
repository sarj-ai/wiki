import { type QueryClient, queryOptions } from "@tanstack/react-query";

import { captureMutationSession } from "@/lib/mutation-session";
import { WIKI_READ_API } from "@/lib/wiki-read-api";

const RECENT_PAGES_STALE_MS = 30_000;
const RECENT_PAGES_GC_MS = 5 * 60_000;
const MAX_RECENT_QUERIES = 8;

export type RecentPagesScope = ReturnType<typeof createRecentPagesScope>;

export function createRecentPagesScope({
  queryClient,
  userId,
  ownerId,
}: {
  queryClient: QueryClient;
  userId: string;
  ownerId: string;
}) {
  let lifetime = new AbortController();
  let authority = new AbortController();
  let generation = 0;
  const listeners = new Set<() => void>();
  const queryKey: readonly string[] = ["wiki", "recent-pages", ownerId, userId];
  const notify = () => {
    for (const listener of listeners) listener();
  };
  const discardQueries = () => {
    void queryClient
      .cancelQueries({ queryKey }, { silent: true })
      .catch(() => console.warn("Recent-page cancellation could not finish"));
    queryClient.removeQueries({ queryKey });
  };
  return {
    activate: () => {
      if (lifetime.signal.aborted) {
        lifetime = new AbortController();
        authority = new AbortController();
        generation += 1;
        notify();
      }
    },
    capture: () => ({
      generation,
      signal: AbortSignal.any([lifetime.signal, authority.signal]),
    }),
    get generation() {
      return generation;
    },
    getSnapshot: () => (lifetime.signal.aborted ? null : generation),
    isActive: () => !lifetime.signal.aborted,
    queryClient,
    queryKey,
    retire: () => {
      lifetime.abort();
      authority.abort();
      discardQueries();
      notify();
    },
    retireAuthority: () => {
      authority.abort();
      authority = new AbortController();
      generation += 1;
      discardQueries();
      notify();
    },
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    userId,
  };
}

export function recentPagesQueryOptions(scope: RecentPagesScope, fileIds: readonly string[]) {
  const ids = [...fileIds];
  const authority = scope.capture();
  // A new authority key cannot reuse an initial read that started before a denial.
  const queryKey = [...scope.queryKey, authority.generation, ...ids];
  const failed = scope.queryClient.getQueryState(queryKey)?.status === "error";
  return queryOptions({
    gcTime: RECENT_PAGES_GC_MS,
    queryFn: async ({ signal }) => {
      const session = captureMutationSession(scope.queryClient);
      const requestSignal = AbortSignal.any([authority.signal, session, signal]);
      requestSignal.throwIfAborted();
      const pages = ids.length === 0 ? [] : await WIKI_READ_API.getRecentFiles(ids, requestSignal);
      requestSignal.throwIfAborted();
      return pages;
    },
    queryKey,
    refetchOnMount: true,
    retry: false,
    staleTime: failed ? 0 : RECENT_PAGES_STALE_MS,
  });
}

export function subscribeRecentPagesInvalidation(scope: RecentPagesScope): () => void {
  const { queryClient, queryKey } = scope;
  const stopQueries = queryClient.getQueryCache().subscribe((event) => {
    if (!scope.isActive()) return;
    const rawKey: unknown = event.query.queryKey;
    if (!Array.isArray(rawKey)) return;
    const key: readonly unknown[] = rawKey;
    if (key[0] === "wiki" && key[1] === "recent-pages" && key[2] === queryKey[2]) {
      if (event.type === "added") pruneRecentPages(scope, event.query.queryHash);
      return;
    }
    if (isFileAuthorityChange(event.type, key, event.type === "updated" ? event.action.type : "")) {
      const affected = queryClient
        .getQueryCache()
        .findAll({ queryKey })
        .some(
          (query) =>
            query.queryKey.includes(key[2], queryKey.length + 1) &&
            (event.type !== "updated" ||
              event.action.type !== "success" ||
              query.state.fetchStatus !== "idle" ||
              !hasMatchingRecentFile(query.state.data, event.query.state.data, key[2])),
        );
      if (affected) scope.retireAuthority();
    }
  });
  const stopMutations = queryClient.getMutationCache().subscribe((event) => {
    if (scope.isActive() && event.type === "updated" && event.action.type === "success") {
      scope.retireAuthority();
    }
  });
  return () => {
    stopQueries();
    stopMutations();
  };
}

function isFileAuthorityChange(type: string, key: readonly unknown[], action: string): boolean {
  return (
    key[0] === "wiki" &&
    key[1] === "file" &&
    (type === "removed" ||
      (type === "updated" &&
        (action === "success" || action === "invalidate" || action === "error")))
  );
}

function hasMatchingRecentFile(data: unknown, file: unknown, fileId: unknown): boolean {
  if (!Array.isArray(data) || !isMetadataRecord(file)) return false;
  const pages: readonly unknown[] = data;
  return pages.some(
    (page) =>
      isMetadataRecord(page) &&
      page["id"] === fileId &&
      file["id"] === fileId &&
      page["name"] === file["name"] &&
      page["mimeType"] === file["mimeType"] &&
      page["driveName"] === (file["driveName"] ?? null),
  );
}

function isMetadataRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object";
}

function pruneRecentPages(scope: RecentPagesScope, currentHash: string): void {
  const queries = scope.queryClient.getQueryCache().findAll({ queryKey: scope.queryKey });
  let excess = queries.length - MAX_RECENT_QUERIES;
  if (excess <= 0) return;
  const removable = queries
    .filter((query) => query.queryHash !== currentHash && query.getObserversCount() === 0)
    .toSorted((a, b) => a.state.dataUpdatedAt - b.state.dataUpdatedAt);
  for (const query of removable) {
    if (excess-- <= 0) break;
    scope.queryClient.removeQueries({ exact: true, queryKey: query.queryKey });
  }
}

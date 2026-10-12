"use client";

import { CancelledError, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { RecentPages } from "@/components/recent-pages/recent-pages";
import { useRecentPagesScope } from "@/components/recent-pages/recent-pages-session";
import { readRecentDocs } from "@/lib/last-doc";
import { recentPagesQueryOptions, type RecentPagesScope } from "@/lib/recent-pages-query";

export function RecentPagesLoader() {
  const scope = useRecentPagesScope();
  if (scope === null) return <RecentPages loading pages={[]} />;
  return <UserRecentPages key={scope.queryKey[2]} scope={scope} />;
}

function UserRecentPages({ scope }: { scope: RecentPagesScope }) {
  const [fileIds, setFileIds] = useState(() =>
    readRecentDocs(scope.userId).map((visit) => visit.fileId),
  );
  const [retrying, setRetrying] = useState(false);
  const query = useQuery(recentPagesQueryOptions(scope, fileIds));
  const loading = retrying || query.isPending || query.isFetching;
  const error = query.isError && !loading;
  return (
    <RecentPages
      error={error}
      loading={loading}
      loadingCount={fileIds.length}
      onRetry={() => {
        setRetrying(true);
        const ids = readRecentDocs(scope.userId).map((visit) => visit.fileId);
        setFileIds(ids);
        const retry = async () => {
          try {
            await scope.queryClient.fetchQuery({
              ...recentPagesQueryOptions(scope, ids),
              staleTime: 0,
            });
          } catch (failure: unknown) {
            if (scope.isActive() && !(failure instanceof CancelledError)) {
              console.warn("Recent-page retry could not finish");
            }
          } finally {
            setRetrying(false);
          }
        };
        void retry().catch(() => console.warn("Recent-page retry could not finish"));
      }}
      pages={query.isSuccess && !loading ? query.data : []}
    />
  );
}

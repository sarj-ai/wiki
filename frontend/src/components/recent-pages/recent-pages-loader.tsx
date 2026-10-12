"use client";

import { useEffect, useState } from "react";

import { type RecentPage, RecentPages } from "@/components/recent-pages/recent-pages";
import { useBrowserUserId } from "@/lib/browser-user-context";
import { readRecentDocs } from "@/lib/last-doc";
import { WIKI_READ_API } from "@/lib/wiki-read-api";

export function RecentPagesLoader() {
  const userId = useBrowserUserId();
  if (!userId) return <RecentPages loading pages={[]} />;
  return <UserRecentPages key={userId} userId={userId} />;
}

function UserRecentPages({ userId }: { userId: string }) {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<{
    pages: RecentPage[];
    status: "error" | "loading" | "ready";
  }>({ pages: [], status: "loading" });
  useEffect(() => {
    const lifetime = new AbortController();
    const visits = readRecentDocs(userId);
    const load = async () => {
      const accessible =
        visits.length === 0
          ? []
          : await WIKI_READ_API.getRecentFiles(
              visits.map((visit) => visit.fileId),
              lifetime.signal,
            );
      if (lifetime.signal.aborted) return;
      setState({ pages: accessible, status: "ready" });
    };
    void load().catch(() => {
      if (lifetime.signal.aborted) return;
      setState({ pages: [], status: "error" });
    });
    return () => lifetime.abort();
  }, [attempt, userId]);
  return (
    <RecentPages
      error={state.status === "error"}
      loading={state.status === "loading"}
      onRetry={() => {
        setState({ pages: [], status: "loading" });
        setAttempt((current) => current + 1);
      }}
      pages={state.pages}
    />
  );
}

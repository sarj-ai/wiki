"use client";

import { useEffect, useRef } from "react";

import { useCurrentUser } from "@/hooks/use-current-user";
import { recordRecentAlias } from "@/lib/recent-aliases";

export function QuickLinkVisitRecorder({ alias }: { alias: string }) {
  const userId = useCurrentUser().data?.id;
  const recorded = useRef<{ alias: string; userId: string } | null>(null);

  useEffect(() => {
    if (userId === undefined) return;
    if (recorded.current?.alias === alias && recorded.current.userId === userId) return;
    recordRecentAlias(userId, alias);
    recorded.current = { alias, userId };
    const url = new URL(window.location.href);
    url.searchParams.delete("go");
    window.history.replaceState(
      window.history.state,
      "",
      `${url.pathname}${url.search}${url.hash}`,
    );
  }, [alias, userId]);

  return null;
}

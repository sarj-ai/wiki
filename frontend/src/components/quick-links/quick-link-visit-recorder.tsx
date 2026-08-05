"use client";

import { useEffect } from "react";

import { useCurrentUser } from "@/hooks/use-current-user";
import { recordRecentAlias } from "@/lib/recent-aliases";

export function QuickLinkVisitRecorder({ alias }: { alias: string }) {
  const user = useCurrentUser().data;

  useEffect(() => {
    if (user === undefined) return;
    recordRecentAlias(user.id, alias);
    const url = new URL(window.location.href);
    url.searchParams.delete("go");
    window.history.replaceState(
      window.history.state,
      "",
      `${url.pathname}${url.search}${url.hash}`,
    );
  }, [alias, user]);

  return null;
}

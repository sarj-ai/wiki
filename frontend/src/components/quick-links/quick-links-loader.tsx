"use client";

import { useEffect, useState } from "react";

import { getRecentDocumentAliasesAction } from "@/app/actions/wiki-actions";
import {
  type QuickLinkItem,
  QuickLinks,
} from "@/components/quick-links/quick-links";
import { useCurrentUser } from "@/hooks/use-current-user";
import { runAction } from "@/lib/action-client";
import type { RecentDocumentAlias } from "@/lib/alias-client";
import { readRecentAliases, writeRecentAliases } from "@/lib/recent-aliases";

type AliasLoad =
  | { kind: "waiting-for-user" }
  | { kind: "loading"; userId: string }
  | { kind: "ready"; userId: string; aliases: QuickLinkItem[] }
  | { kind: "failed"; userId: string };

export function QuickLinksLoader() {
  const userQuery = useCurrentUser();
  const userId = userQuery.data?.id;
  const [state, setState] = useState<AliasLoad>(() =>
    userId === undefined
      ? { kind: "waiting-for-user" }
      : { kind: "loading", userId },
  );
  const owner = state.kind === "waiting-for-user" ? undefined : state.userId;
  if (owner !== userId) {
    setState(
      userId === undefined
        ? { kind: "waiting-for-user" }
        : { kind: "loading", userId },
    );
  }

  useEffect(() => {
    if (userId === undefined) return;
    let cancelled = false;
    const load = async () => {
      const items = await loadRecentForUser(userId, () => !cancelled);
      if (cancelled) return;
      setState({ kind: "ready", userId, aliases: items });
    };
    void load().catch(() => {
      if (cancelled) return;
      setState({ kind: "failed", userId });
    });
    return () => {
      cancelled = true;
    };
  }, [userId]);

  if (userQuery.isError) return <QuickLinks aliases={[]} error />;
  if (owner !== userId) return <QuickLinks aliases={[]} loading />;
  switch (state.kind) {
    case "waiting-for-user":
    case "loading":
      return <QuickLinks aliases={[]} loading />;
    case "ready":
      return <QuickLinks aliases={state.aliases} />;
    case "failed":
      return <QuickLinks aliases={[]} error />;
    default:
      return unreachable(state);
  }
}

async function loadRecentForUser(
  userId: string,
  isCurrent: () => boolean,
): Promise<QuickLinkItem[]> {
  const visits = readRecentAliases(userId);
  if (visits.length === 0) return [];
  const accessible = await runAction(
    async () =>
      await getRecentDocumentAliasesAction(visits.map((visit) => visit.alias)),
  );
  const byAlias = new Map(accessible.map((item) => [item.alias, item]));
  const retainedVisits = visits.filter((visit) => byAlias.has(visit.alias));
  const deniedVisits = new Map(
    visits
      .filter((visit) => !byAlias.has(visit.alias))
      .map((visit) => [visit.alias, visit.visitedAt]),
  );
  if (isCurrent() && deniedVisits.size > 0) {
    const currentVisits = readRecentAliases(userId);
    const nextVisits = currentVisits.filter(
      (visit) => deniedVisits.get(visit.alias) !== visit.visitedAt,
    );
    if (nextVisits.length !== currentVisits.length)
      writeRecentAliases(userId, nextVisits);
  }
  return mergeVisits(retainedVisits, byAlias);
}

function mergeVisits(
  visits: ReturnType<typeof readRecentAliases>,
  byAlias: Map<string, RecentDocumentAlias>,
): QuickLinkItem[] {
  const links: QuickLinkItem[] = [];
  for (const visit of visits) {
    const item = byAlias.get(visit.alias);
    if (item !== undefined)
      links.push({ ...item, lastVisitedAt: visit.visitedAt });
  }
  return links;
}

function unreachable(value: never): never {
  throw new Error(`Unsupported alias load state: ${String(value)}`);
}

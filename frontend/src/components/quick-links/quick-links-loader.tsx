"use client";

import { useEffect, useState } from "react";

import { getRecentDocumentAliasesAction } from "@/app/actions/wiki-actions";
import { type QuickLinkItem, QuickLinks } from "@/components/quick-links/quick-links";
import { useCurrentUser } from "@/hooks/use-current-user";
import { runAction } from "@/lib/action-client";
import type { RecentDocumentAlias } from "@/lib/alias-client";
import { readRecentAliases, writeRecentAliases } from "@/lib/recent-aliases";

export function QuickLinksLoader() {
  const userQuery = useCurrentUser();
  const [aliases, setAliases] = useState<QuickLinkItem[]>([]);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(true);
  const [validatedUserId, setValidatedUserId] = useState<null | string>(null);
  const userId = userQuery.data?.id;

  useEffect(() => {
    if (userId === undefined) return;
    let cancelled = false;
    const load = async () => {
      const items = await loadRecentForUser(userId);
      if (cancelled) return;
      setAliases(items);
      setError(false);
      setValidatedUserId(userId);
    };
    void load()
      .catch(() => {
        if (cancelled) return;
        setError(true);
        setValidatedUserId(userId);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [userId]);

  if (userQuery.isError) return <QuickLinks aliases={[]} error />;
  const isCurrentUserValidated = userId !== undefined && validatedUserId === userId;
  const showError = isCurrentUserValidated && error;
  return (
    <QuickLinks
      aliases={isCurrentUserValidated ? aliases : []}
      error={showError}
      loading={!isCurrentUserValidated || loading}
    />
  );
}

async function loadRecentForUser(userId: string): Promise<QuickLinkItem[]> {
  const visits = readRecentAliases(userId);
  if (visits.length === 0) return [];
  const accessible = await runAction(
    async () => await getRecentDocumentAliasesAction(visits.map((visit) => visit.alias)),
  );
  const byAlias = new Map(accessible.map((item) => [item.alias, item]));
  const retainedVisits = visits.filter((visit) => byAlias.has(visit.alias));
  const deniedVisits = new Map(
    visits
      .filter((visit) => !byAlias.has(visit.alias))
      .map((visit) => [visit.alias, visit.visitedAt]),
  );
  if (deniedVisits.size > 0) {
    // Authorization applies to the visit snapshot. A navigation or another tab
    // may have added a visit while the request was pending.
    const currentVisits = readRecentAliases(userId);
    const nextVisits = currentVisits.filter(
      (visit) => deniedVisits.get(visit.alias) !== visit.visitedAt,
    );
    if (nextVisits.length !== currentVisits.length) writeRecentAliases(userId, nextVisits);
  }
  return mergeVisits(retainedVisits, byAlias);
}

function mergeVisits(
  visits: ReturnType<typeof readRecentAliases>,
  byAlias: Map<string, RecentDocumentAlias>,
): QuickLinkItem[] {
  return visits.flatMap((visit) => {
    const item = byAlias.get(visit.alias);
    return item === undefined ? [] : [{ ...item, lastVisitedAt: visit.visitedAt }];
  });
}

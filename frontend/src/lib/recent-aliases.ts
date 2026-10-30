import { recentAliasesStorageKey } from "@/lib/browser-storage";
import { RecentAliasVisitSchema, RecentAliasVisitsSchema } from "@/lib/storage-schemas";

export const MAX_RECENT_ALIASES = 50;

export interface RecentAliasVisit {
  alias: string;
  visitedAt: string;
}

export function recordRecentAlias(userId: string, alias: string): void {
  const visitedAt = new Date().toISOString();
  const visits = readRecentAliases(userId);
  const parsed = RecentAliasVisitSchema.safeParse({ alias, visitedAt });
  const next = parsed.success
    ? [parsed.data, ...visits.filter((visit) => visit.alias !== alias)].slice(0, MAX_RECENT_ALIASES)
    : [];
  persistRecentAliases(userId, next);
}

export function readRecentAliases(userId: string): RecentAliasVisit[] {
  try {
    const raw = window.localStorage.getItem(recentAliasesStorageKey(userId)) ?? "[]";
    return RecentAliasVisitsSchema.parse(JSON.parse(raw));
  } catch {
    return [];
  }
}

export function writeRecentAliases(userId: string, visits: RecentAliasVisit[]): void {
  try {
    const capped = RecentAliasVisitsSchema.parse(visits.slice(0, MAX_RECENT_ALIASES));
    persistRecentAliases(userId, capped);
  } catch {
    // Browser storage can be unavailable or full; quick links remain optional.
  }
}

function persistRecentAliases(userId: string, visits: RecentAliasVisit[]): void {
  try {
    window.localStorage.setItem(recentAliasesStorageKey(userId), JSON.stringify(visits));
  } catch {
    // Browser storage can be unavailable or full; quick links remain optional.
  }
}

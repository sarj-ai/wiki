// @vitest-environment happy-dom

import {
  MAX_RECENT_ALIASES,
  readRecentAliases,
  recordRecentAlias,
  writeRecentAliases,
} from "@/lib/recent-aliases";
import { installLocalStorage } from "@/test-stubs/local-storage";

describe("recent aliases", () => {
  beforeEach(() => {
    installLocalStorage();
    window.localStorage.clear();
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-04T12:00:00Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("keeps a per-user hard cap of 50 in most-recent-first order", () => {
    const visits = Array.from({ length: 60 }, (_, index) => ({
      alias: `item-${String.fromCodePoint(97 + Math.floor(index / 26))}${String.fromCodePoint(97 + (index % 26))}`,
      visitedAt: new Date(1_700_000_000_000 + index).toISOString(),
    }));

    writeRecentAliases("user-1", visits);

    expect(readRecentAliases("user-1")).toHaveLength(MAX_RECENT_ALIASES);
    expect(readRecentAliases("user-2")).toEqual([]);
  });

  it("moves a repeated alias to the front without duplicating it", () => {
    recordRecentAlias("user-1", "team-handbook");
    recordRecentAlias("user-1", "launch-roadmap");
    recordRecentAlias("user-1", "team-handbook");

    expect(readRecentAliases("user-1").map((visit) => visit.alias)).toEqual([
      "team-handbook",
      "launch-roadmap",
    ]);
  });
});

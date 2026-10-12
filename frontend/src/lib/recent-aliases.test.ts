// @vitest-environment happy-dom

import {
  MAX_RECENT_ALIASES,
  readRecentAliases,
  recordRecentAlias,
  writeRecentAliases,
} from "@/lib/recent-aliases";
import { installLocalStorage } from "@/test-stubs/install-local-storage";

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

  it("keeps the whole-list empty fallback when a newly recorded alias is invalid", () => {
    recordRecentAlias("user-1", "team-handbook");
    recordRecentAlias("user-1", "INVALID");

    expect(readRecentAliases("user-1")).toEqual([]);
    expect(window.localStorage.getItem("sarj-wiki:recent-go:user-1")).toBe("[]");
  });

  it("continues validating arbitrary caller-provided visits before writing them", () => {
    recordRecentAlias("user-1", "team-handbook");
    writeRecentAliases("user-1", [{ alias: "team-handbook", visitedAt: "yesterday" }]);

    expect(readRecentAliases("user-1")).toEqual([]);
    expect(window.localStorage.getItem("sarj-wiki:recent-go:user-1")).toBe("[]");
  });

  it("records a new visit when the optional history read fails but writes work", () => {
    const read = vi.spyOn(window.localStorage, "getItem").mockImplementation(() => {
      throw new Error("Storage read unavailable");
    });

    recordRecentAlias("user-1", "team-handbook");
    read.mockRestore();

    expect(readRecentAliases("user-1")).toEqual([
      { alias: "team-handbook", visitedAt: "2026-08-04T12:00:00.000Z" },
    ]);
  });

  it("keeps an unavailable visit write optional", () => {
    const write = vi.spyOn(window.localStorage, "setItem").mockImplementation(() => {
      throw new Error("Storage write unavailable");
    });

    expect(() => recordRecentAlias("user-1", "team-handbook")).not.toThrow();
    write.mockRestore();
    expect(readRecentAliases("user-1")).toEqual([]);
  });

  it("owns each invalid-history fallback even when a caller mutates a returned array", () => {
    window.localStorage.setItem(
      "sarj-wiki:recent-go:user-1",
      JSON.stringify([{ alias: "INVALID", visitedAt: "yesterday" }]),
    );
    window.localStorage.setItem("sarj-wiki:recent-go:user-2", "null");
    const rejected = readRecentAliases("user-1");
    expect(rejected).toEqual([]);

    rejected.push({ alias: "INVALID", visitedAt: "yesterday" });

    expect(readRecentAliases("user-1")).toEqual([]);
    expect(readRecentAliases("user-2")).toEqual([]);
    recordRecentAlias("user-2", "team-handbook");
    expect(readRecentAliases("user-2")).toEqual([
      { alias: "team-handbook", visitedAt: "2026-08-04T12:00:00.000Z" },
    ]);
  });
});

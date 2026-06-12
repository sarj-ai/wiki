import { describe, expect, it } from "vitest";

import {
  ZAuthErrorParam,
  ZLastRefreshMs,
  ZPersistedExpansion,
  ZSidebarStateCookie,
  ZSidebarWidthCss,
} from "@/lib/storage-schemas";

describe("ZLastRefreshMs", () => {
  it("coerces a numeric string", () => {
    expect(ZLastRefreshMs.parse("123")).toBe(123);
  });

  it("falls back to 0 on garbage or null", () => {
    expect(ZLastRefreshMs.parse("not-a-number")).toBe(0);
    expect(ZLastRefreshMs.parse(null)).toBe(0);
    expect(ZLastRefreshMs.parse("-5")).toBe(0);
  });
});

describe("ZSidebarWidthCss", () => {
  it("accepts a px value and rejects anything else", () => {
    expect(ZSidebarWidthCss.parse("320px")).toBe("320px");
    expect(ZSidebarWidthCss.parse("100%")).toBeNull();
    expect(ZSidebarWidthCss.parse(null)).toBeNull();
  });
});

describe("ZSidebarStateCookie", () => {
  it("maps to a boolean and defaults to true", () => {
    const missing: string | undefined = undefined;
    expect(ZSidebarStateCookie.parse("false")).toBe(false);
    expect(ZSidebarStateCookie.parse("true")).toBe(true);
    expect(ZSidebarStateCookie.parse(missing)).toBe(true);
    expect(ZSidebarStateCookie.parse("garbage")).toBe(true);
  });
});

describe("ZAuthErrorParam", () => {
  it("accepts known keys and rejects unknown", () => {
    expect(ZAuthErrorParam.safeParse("auth_failed").success).toBe(true);
    expect(ZAuthErrorParam.safeParse("../etc").success).toBe(false);
    expect(ZAuthErrorParam.safeParse(["array"]).success).toBe(false);
  });
});

describe("ZPersistedExpansion", () => {
  it("accepts a valid envelope and rejects malformed shapes", () => {
    expect(
      ZPersistedExpansion.safeParse({ state: { expanded: { f1: true } }, version: 0 }).success,
    ).toBe(true);
    expect(ZPersistedExpansion.safeParse({ state: { expanded: { f1: false } } }).success).toBe(
      false,
    );
    expect(ZPersistedExpansion.safeParse("nope").success).toBe(false);
  });
});

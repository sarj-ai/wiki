import { describe, expect, it } from "vitest";

import {
  ZAUTH_ERROR_PARAM,
  ZLAST_REFRESH_MS,
  ZPERSISTED_DRIVE_EXPANSION,
  ZPERSISTED_EXPANSION,
  ZSIDEBAR_STATE_COOKIE,
  ZSIDEBAR_WIDTH_CSS,
} from "@/lib/storage-schemas";

describe("ZLAST_REFRESH_MS", () => {
  it("coerces a numeric string", () => {
    expect(ZLAST_REFRESH_MS.parse("123")).toBe(123);
  });

  it("falls back to 0 on garbage or null", () => {
    expect(ZLAST_REFRESH_MS.parse("not-a-number")).toBe(0);
    expect(ZLAST_REFRESH_MS.parse(null)).toBe(0);
    expect(ZLAST_REFRESH_MS.parse("-5")).toBe(0);
  });
});

describe("ZSIDEBAR_WIDTH_CSS", () => {
  it("accepts a px value and rejects anything else", () => {
    expect(ZSIDEBAR_WIDTH_CSS.parse("320px")).toBe("320px");
    expect(ZSIDEBAR_WIDTH_CSS.parse("100%")).toBeNull();
    expect(ZSIDEBAR_WIDTH_CSS.parse(null)).toBeNull();
  });
});

describe("ZSIDEBAR_STATE_COOKIE", () => {
  it("maps to a boolean and defaults to true", () => {
    const missing: string | undefined = undefined;
    expect(ZSIDEBAR_STATE_COOKIE.parse("false")).toBe(false);
    expect(ZSIDEBAR_STATE_COOKIE.parse("true")).toBe(true);
    expect(ZSIDEBAR_STATE_COOKIE.parse(missing)).toBe(true);
    expect(ZSIDEBAR_STATE_COOKIE.parse("garbage")).toBe(true);
  });
});

describe("ZAUTH_ERROR_PARAM", () => {
  it("accepts known keys and rejects unknown", () => {
    expect(ZAUTH_ERROR_PARAM.safeParse("auth_failed").success).toBe(true);
    expect(ZAUTH_ERROR_PARAM.safeParse("../etc").success).toBe(false);
    expect(ZAUTH_ERROR_PARAM.safeParse(["array"]).success).toBe(false);
  });
});

describe("ZPERSISTED_EXPANSION", () => {
  it("accepts a valid envelope and rejects malformed shapes", () => {
    expect(
      ZPERSISTED_EXPANSION.safeParse({ state: { expanded: { f1: true } }, version: 0 }).success,
    ).toBe(true);
    expect(ZPERSISTED_EXPANSION.safeParse({ state: { expanded: { f1: false } } }).success).toBe(
      false,
    );
    expect(ZPERSISTED_EXPANSION.safeParse("nope").success).toBe(false);
  });
});

describe("ZPERSISTED_DRIVE_EXPANSION", () => {
  it("accepts only the explicit version-one representation", () => {
    expect(
      ZPERSISTED_DRIVE_EXPANSION.safeParse({
        state: { expanded: { d1: true }, initialized: true },
        version: 1,
      }).success,
    ).toBe(true);
    expect(
      ZPERSISTED_DRIVE_EXPANSION.safeParse({
        state: { collapsed: { d1: true } },
        version: 0,
      }).success,
    ).toBe(false);
  });
});

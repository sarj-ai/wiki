import { describe, expect, it } from "vitest";

import {
  AuthErrorParamSchema,
  LastRefreshMsSchema,
  PersistedDriveExpansionSchema,
  PersistedExpansionSchema,
  SidebarStateCookieSchema,
  SidebarWidthCssSchema,
} from "@/lib/storage-schemas";

describe("ZLAST_REFRESH_MS", () => {
  it("coerces a numeric string", () => {
    expect(LastRefreshMsSchema.parse("123")).toBe(123);
  });

  it("falls back to 0 on garbage or null", () => {
    expect(LastRefreshMsSchema.parse("not-a-number")).toBe(0);
    expect(LastRefreshMsSchema.parse(null)).toBe(0);
    expect(LastRefreshMsSchema.parse("-5")).toBe(0);
  });
});

describe("ZSIDEBAR_WIDTH_CSS", () => {
  it("accepts a px value and rejects anything else", () => {
    expect(SidebarWidthCssSchema.parse("320px")).toBe("320px");
    expect(SidebarWidthCssSchema.parse("100%")).toBeNull();
    expect(SidebarWidthCssSchema.parse(null)).toBeNull();
  });
});

describe("ZSIDEBAR_STATE_COOKIE", () => {
  it("maps to a boolean and defaults to true", () => {
    const missing: string | undefined = undefined;
    expect(SidebarStateCookieSchema.parse("false")).toBe(false);
    expect(SidebarStateCookieSchema.parse("true")).toBe(true);
    expect(SidebarStateCookieSchema.parse(missing)).toBe(true);
    expect(SidebarStateCookieSchema.parse("garbage")).toBe(true);
  });
});

describe("ZAUTH_ERROR_PARAM", () => {
  it("accepts known keys and rejects unknown", () => {
    expect(AuthErrorParamSchema.safeParse("auth_failed").success).toBe(true);
    expect(AuthErrorParamSchema.safeParse("../etc").success).toBe(false);
    expect(AuthErrorParamSchema.safeParse(["array"]).success).toBe(false);
  });
});

describe("ZPERSISTED_EXPANSION", () => {
  it("accepts a valid envelope and rejects malformed shapes", () => {
    expect(
      PersistedExpansionSchema.safeParse({ state: { expanded: { f1: true } }, version: 0 }).success,
    ).toBe(true);
    expect(PersistedExpansionSchema.safeParse({ state: { expanded: { f1: false } } }).success).toBe(
      false,
    );
    expect(PersistedExpansionSchema.safeParse("nope").success).toBe(false);
  });
});

describe("ZPERSISTED_DRIVE_EXPANSION", () => {
  it("accepts only the explicit version-one representation", () => {
    expect(
      PersistedDriveExpansionSchema.safeParse({
        state: { expanded: { d1: true }, initialized: true },
        version: 1,
      }).success,
    ).toBe(true);
    expect(
      PersistedDriveExpansionSchema.safeParse({
        state: { collapsed: { d1: true } },
        version: 0,
      }).success,
    ).toBe(false);
  });
});

import { describe, expect, it } from "vitest";

import { warmCountFor } from "./doc-warmup-policy";

describe("document warmup policy", () => {
  it("warms only the most likely document on capable devices", () => {
    expect(warmCountFor({})).toBe(1);
  });

  it("warms one document on capable devices with hints", () => {
    expect(warmCountFor({ deviceMemory: 4 })).toBe(1);
  });

  it("does no background iframe work on constrained devices", () => {
    expect(warmCountFor({ connection: { saveData: true } })).toBe(0);
    expect(warmCountFor({ connection: { effectiveType: "slow-2g" } })).toBe(0);
    expect(warmCountFor({ connection: { effectiveType: "2g" } })).toBe(0);
    expect(warmCountFor({ deviceMemory: 2 })).toBe(0);
  });
});

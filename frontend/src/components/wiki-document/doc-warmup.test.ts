import { describe, expect, it } from "vitest";

import { warmCountFor } from "./doc-warmup-policy";

describe("document warmup policy", () => {
  it("warms only the most likely document on capable devices", () => {
    expect(warmCountFor({})).toBe(1);
  });

  it("warms one document on constrained devices", () => {
    expect(warmCountFor({ connection: { effectiveType: "2g" } })).toBe(1);
    expect(warmCountFor({ deviceMemory: 4 })).toBe(1);
  });

  it("does no background network work when Save-Data is enabled", () => {
    expect(warmCountFor({ connection: { saveData: true } })).toBe(0);
  });
});

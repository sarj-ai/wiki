import { describe, expect, it } from "vitest";
import { fractionalIndexFor } from "./fractional-index";

describe("fractionalIndexFor", () => {
  const so = (map: Record<string, number>) => (id: string) => map[id];

  it("places a moved item between two overrides", () => {
    const result = fractionalIndexFor(["a", "b", "c", "x"], 2, "x", so({ a: 1, b: 2, c: 3 }));
    // With moved removed: [a, b, c]. newIndex 2 → between b(2) and c(3).
    expect(result).toBe(2.5);
  });

  it("appends when moved to the end", () => {
    const result = fractionalIndexFor(["a", "b", "x"], 2, "x", so({ a: 1, b: 2 }));
    // With moved removed: [a, b]. newIndex 2 → after b(2).
    expect(result).toBe(3);
  });

  it("prepends when moved to the start", () => {
    const result = fractionalIndexFor(["a", "b", "x"], 0, "x", so({ a: 5, b: 7 }));
    // With moved removed: [a, b]. newIndex 0 → before a(5).
    expect(result).toBe(4);
  });

  it("falls back to the neighbor's array index when it has no override", () => {
    const result = fractionalIndexFor(["a", "b", "c", "x"], 1, "x", so({}));
    // With moved removed: [a, b, c]. newIndex 1 → between a(idx 0) and b(idx 1).
    expect(result).toBe(0.5);
  });

  it("returns 0 for an empty list", () => {
    const result = fractionalIndexFor(["x"], 0, "x", so({}));
    expect(result).toBe(0);
  });
});

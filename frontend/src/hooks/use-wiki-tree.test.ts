import { driveTreeQueryOptions } from "@/hooks/use-wiki-tree";

describe("Drive tree query gating", () => {
  it("disables the query while its Drive is collapsed", () => {
    expect(driveTreeQueryOptions("drive-1", false).enabled).toBe(false);
    expect(driveTreeQueryOptions("drive-1", true).enabled).toBe(true);
    expect(driveTreeQueryOptions(null, true).enabled).toBe(false);
  });
});

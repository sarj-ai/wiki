import { dragScopeData, sortableContainerId, sortableData, sortablePosition } from "./drag-data";

describe("drag metadata validation", () => {
  it.each([null, undefined, {}, { sortable: null }, { sortable: { containerId: 123 } }])(
    "does not accept an invalid container: %j",
    (data) => {
      expect(sortableContainerId(data)).toBeNull();
    },
  );

  it("validates only the container for hover without reading the sibling list", () => {
    const readItems = vi.fn(() => ["one", "two"]);
    const sortable = {
      containerId: "parent:folder",
      get items() {
        return readItems();
      },
    };
    expect(sortableContainerId({ sortable })).toBe("parent:folder");
    expect(readItems).not.toHaveBeenCalled();
    expect(sortableData({ sortable })).toEqual({
      containerId: "parent:folder",
      items: ["one", "two"],
    });
    expect(readItems).toHaveBeenCalled();
  });

  it("rejects invalid full commit metadata and invalid destination scope", () => {
    expect(sortableData({ sortable: { containerId: "parent:folder", items: [1] } })).toBeNull();
    expect(dragScopeData({ driveId: 1 })).toBeNull();
    expect(dragScopeData({ driveId: "drive", parentFolderId: 1 })).toBeNull();
    expect(dragScopeData({ driveId: "drive", parentFolderId: "folder" })).toEqual({
      driveId: "drive",
      parentFolderId: "folder",
    });
  });

  it("validates native hover indices without reading items and keeps full commit validation", () => {
    const readItems = vi.fn(() => [1]);
    const sortable = {
      containerId: "parent:folder",
      index: 123,
      get items() {
        return readItems();
      },
    };
    expect(sortablePosition({ sortable })).toEqual({ containerId: "parent:folder", index: 123 });
    expect(readItems).not.toHaveBeenCalled();
    expect(sortableData({ sortable })).toBeNull();
    expect(readItems).toHaveBeenCalledOnce();
  });

  it.each([-1, 1.5, NaN, Infinity, "2", undefined])(
    "rejects invalid native hover index %s",
    (index) => {
      expect(sortablePosition({ sortable: { containerId: "parent:folder", index } })).toBeNull();
    },
  );
});

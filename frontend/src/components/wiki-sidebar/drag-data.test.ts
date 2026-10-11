import { dragScopeData, sortableContainerId, sortableData } from "./drag-data";

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
});

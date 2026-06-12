import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";

import type { DriveNode, DriveTree, FileResponse } from "@/types/wiki";

import { wikiKeys } from "@/hooks/use-wiki-tree";
import {
  fileToNode,
  findCachedNodeMeta,
  injectNewFileIntoCaches,
  removeFileFromCaches,
} from "@/lib/wiki-cache";
import { MIME_DOC, MIME_FOLDER, MIME_SHEET, MIME_SHORTCUT } from "@/types/wiki";

function makeFile(overrides: Partial<FileResponse> = {}): FileResponse {
  return {
    id: "file-1",
    name: "Untitled",
    mimeType: MIME_DOC,
    parentFolderId: "folder-a",
    driveId: "drive-1",
    modifiedTime: "2025-01-01T00:00:00Z",
    ...overrides,
  };
}

function makeNode(overrides: Partial<DriveNode> = {}): DriveNode {
  return {
    id: "n",
    name: "n",
    mimeType: MIME_DOC,
    parentId: undefined,
    modifiedTime: undefined,
    shortcutTargetId: undefined,
    shortcutTargetMimeType: undefined,
    shortcutTargetDriveId: undefined,
    shortcutTargetDriveName: undefined,
    shortcutInaccessible: false,
    ...overrides,
  };
}

describe("fileToNode", () => {
  it("projects FileResponse fields onto DriveNode shape", () => {
    const node = fileToNode(makeFile({ id: "f", name: "Doc", parentFolderId: "p" }));
    expect(node).toEqual({
      id: "f",
      name: "Doc",
      mimeType: MIME_DOC,
      parentId: "p",
      modifiedTime: "2025-01-01T00:00:00Z",
      shortcutTargetId: undefined,
      shortcutTargetMimeType: undefined,
      shortcutTargetDriveId: undefined,
      shortcutTargetDriveName: undefined,
      shortcutInaccessible: false,
    });
  });

  it("leaves parentId absent when parentFolderId is missing", () => {
    expect(fileToNode(makeFile({ parentFolderId: undefined })).parentId).toBeUndefined();
  });
});

describe("injectNewFileIntoCaches", () => {
  it("appends to the parent folder-files cache when present", () => {
    const qc = new QueryClient();
    const existing: DriveNode[] = [makeNode({ id: "old", name: "Old" })];
    qc.setQueryData(wikiKeys.folderFiles("folder-a"), existing);

    injectNewFileIntoCaches(qc, makeFile({ id: "new", name: "New" }));

    const updated = qc.getQueryData<DriveNode[]>(wikiKeys.folderFiles("folder-a"));
    expect(updated).toHaveLength(2);
    expect(updated?.[1]?.id).toBe("new");
  });

  it("uses driveId as parent key when parentFolderId is absent", () => {
    const qc = new QueryClient();
    qc.setQueryData<DriveNode[]>(wikiKeys.folderFiles("drive-1"), []);

    injectNewFileIntoCaches(qc, makeFile({ parentFolderId: undefined }));

    expect(qc.getQueryData<DriveNode[]>(wikiKeys.folderFiles("drive-1"))).toHaveLength(1);
  });

  it("skips folder-files cache when no entry exists for the parent", () => {
    const qc = new QueryClient();
    injectNewFileIntoCaches(qc, makeFile());
    expect(qc.getQueryData(wikiKeys.folderFiles("folder-a"))).toBeUndefined();
  });

  it("dedupes when the file is already in the listing", () => {
    const qc = new QueryClient();
    const node = makeNode({ id: "dup", parentId: "folder-a" });
    qc.setQueryData<DriveNode[]>(wikiKeys.folderFiles("folder-a"), [node]);

    injectNewFileIntoCaches(qc, makeFile({ id: "dup" }));

    expect(qc.getQueryData<DriveNode[]>(wikiKeys.folderFiles("folder-a"))).toHaveLength(1);
  });

  it("only updates the drive-tree cache for new folders, not for nested files", () => {
    const qc = new QueryClient();
    const tree: DriveTree = { driveId: "drive-1", folders: [], files: [] };
    qc.setQueryData(wikiKeys.driveTree("drive-1"), tree);

    injectNewFileIntoCaches(qc, makeFile({ mimeType: MIME_DOC, parentFolderId: "folder-a" }));
    const after = qc.getQueryData<DriveTree>(wikiKeys.driveTree("drive-1"));
    expect(after?.folders).toHaveLength(0);
    expect(after?.files).toHaveLength(0);

    injectNewFileIntoCaches(qc, makeFile({ id: "newfolder", mimeType: MIME_FOLDER }));
    expect(qc.getQueryData<DriveTree>(wikiKeys.driveTree("drive-1"))?.folders).toHaveLength(1);
  });

  it("splices root-level files into the drive tree's files list", () => {
    const qc = new QueryClient();
    const tree: DriveTree = { driveId: "drive-1", folders: [], files: [] };
    qc.setQueryData(wikiKeys.driveTree("drive-1"), tree);

    injectNewFileIntoCaches(
      qc,
      makeFile({ id: "rootdoc", mimeType: MIME_DOC, parentFolderId: "drive-1" }),
    );

    const updated = qc.getQueryData<DriveTree>(wikiKeys.driveTree("drive-1"));
    expect(updated?.files.map((f) => f.id)).toEqual(["rootdoc"]);

    removeFileFromCaches(qc, "rootdoc", "drive-1", "drive-1");
    expect(qc.getQueryData<DriveTree>(wikiKeys.driveTree("drive-1"))?.files).toHaveLength(0);
  });
});

describe("findCachedNodeMeta", () => {
  it("finds a file in a folder-files listing", () => {
    const qc = new QueryClient();
    qc.setQueryData<DriveNode[]>(wikiKeys.folderFiles("folder-a"), [
      makeNode({ id: "doc-1", name: "Roadmap", mimeType: MIME_DOC }),
    ]);

    expect(findCachedNodeMeta(qc, "doc-1")).toEqual({ name: "Roadmap", mimeType: MIME_DOC });
  });

  it("finds a folder in a drive tree", () => {
    const qc = new QueryClient();
    const tree: DriveTree = {
      driveId: "drive-1",
      folders: [makeNode({ id: "folder-x", name: "Specs", mimeType: MIME_FOLDER })],
      files: [],
    };
    qc.setQueryData(wikiKeys.driveTree("drive-1"), tree);

    expect(findCachedNodeMeta(qc, "folder-x")).toEqual({ name: "Specs", mimeType: MIME_FOLDER });
  });

  it("resolves a shortcut by its target id with the target's mime type", () => {
    const qc = new QueryClient();
    qc.setQueryData<DriveNode[]>(wikiKeys.folderFiles("folder-a"), [
      makeNode({
        id: "shortcut-1",
        name: "Budget (link)",
        mimeType: MIME_SHORTCUT,
        shortcutTargetId: "sheet-9",
        shortcutTargetMimeType: MIME_SHEET,
      }),
    ]);

    expect(findCachedNodeMeta(qc, "sheet-9")).toEqual({
      name: "Budget (link)",
      mimeType: MIME_SHEET,
    });
    expect(findCachedNodeMeta(qc, "shortcut-1")).toBeNull();
  });

  it("returns null when nothing cached matches", () => {
    const qc = new QueryClient();
    qc.setQueryData<DriveNode[]>(wikiKeys.folderFiles("folder-a"), [makeNode({ id: "other" })]);

    expect(findCachedNodeMeta(qc, "missing")).toBeNull();
  });
});

describe("removeFileFromCaches", () => {
  it("filters the deleted file out of folder-files", () => {
    const qc = new QueryClient();
    qc.setQueryData<DriveNode[]>(wikiKeys.folderFiles("folder-a"), [
      makeNode({ id: "keep" }),
      makeNode({ id: "drop" }),
    ]);

    removeFileFromCaches(qc, "drop", "folder-a", "drive-1");

    const remaining = qc.getQueryData<DriveNode[]>(wikiKeys.folderFiles("folder-a"));
    expect(remaining?.map((n) => n.id)).toEqual(["keep"]);
  });

  it("filters the deleted folder out of the drive tree", () => {
    const qc = new QueryClient();
    const tree: DriveTree = {
      driveId: "drive-1",
      folders: [makeNode({ id: "f1" }), makeNode({ id: "f2" })],
      files: [],
    };
    qc.setQueryData(wikiKeys.driveTree("drive-1"), tree);

    removeFileFromCaches(qc, "f1", undefined, "drive-1");

    const updated = qc.getQueryData<DriveTree>(wikiKeys.driveTree("drive-1"));
    expect(updated?.folders.map((f) => f.id)).toEqual(["f2"]);
  });

  it("clears any per-file metadata cache so re-navigation refetches", () => {
    const qc = new QueryClient();
    qc.setQueryData(["wiki", "file", "x"], { id: "x" });

    removeFileFromCaches(qc, "x");

    expect(qc.getQueryData(["wiki", "file", "x"])).toBeUndefined();
  });
});

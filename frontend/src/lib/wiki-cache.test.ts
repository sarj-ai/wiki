import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";

import { wikiKeys } from "@/hooks/use-wiki-tree";
import {
  fileToNode,
  findCachedNodeMeta,
  injectNewFileIntoCaches,
  optimisticMoveFile,
  removeFileFromCaches,
} from "@/lib/wiki-cache";
import type { DriveNode, DriveTree, FileResponse } from "@/types/wiki";
import { MIME_DOC, MIME_FOLDER, MIME_SHEET, MIME_SHORTCUT } from "@/types/wiki";

function makeFile(overrides: Partial<FileResponse> = {}): FileResponse {
  return {
    canEdit: true,
    driveId: "drive-1",
    id: "file-1",
    mimeType: MIME_DOC,
    modifiedTime: "2025-01-01T00:00:00Z",
    name: "Untitled",
    parentFolderId: "folder-a",
    ...overrides,
  };
}

function makeNode(overrides: Partial<DriveNode> = {}): DriveNode {
  return {
    id: "n",
    mimeType: MIME_DOC,
    modifiedTime: undefined,
    name: "n",
    parentId: undefined,
    shortcutInaccessible: false,
    shortcutTargetDriveId: undefined,
    shortcutTargetDriveName: undefined,
    shortcutTargetId: undefined,
    shortcutTargetMimeType: undefined,
    ...overrides,
  };
}

describe("fileToNode", () => {
  it("projects FileResponse fields onto DriveNode shape", () => {
    const node = fileToNode(makeFile({ id: "f", name: "Doc", parentFolderId: "p" }));
    expect(node).toEqual({
      id: "f",
      mimeType: MIME_DOC,
      modifiedTime: "2025-01-01T00:00:00Z",
      name: "Doc",
      parentId: "p",
      shortcutInaccessible: false,
      shortcutTargetDriveId: undefined,
      shortcutTargetDriveName: undefined,
      shortcutTargetId: undefined,
      shortcutTargetMimeType: undefined,
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

  it("splices nested files into tree.files with their parent retained", () => {
    const qc = new QueryClient();
    const tree: DriveTree = { driveId: "drive-1", files: [], filesComplete: true, folders: [] };
    qc.setQueryData(wikiKeys.driveTree("drive-1"), tree);

    injectNewFileIntoCaches(qc, makeFile({ mimeType: MIME_DOC, parentFolderId: "folder-a" }));
    const after = qc.getQueryData<DriveTree>(wikiKeys.driveTree("drive-1"));
    expect(after?.folders).toHaveLength(0);
    expect(after?.files.map((f) => f.parentId)).toEqual(["folder-a"]);

    injectNewFileIntoCaches(qc, makeFile({ id: "newfolder", mimeType: MIME_FOLDER }));
    const folder = qc.getQueryData<DriveTree>(wikiKeys.driveTree("drive-1"))?.folders[0];
    expect(folder?.parentId).toBe("folder-a");
  });

  it("normalizes a newly-created root folder into the tree root", () => {
    const qc = new QueryClient();
    const tree: DriveTree = { driveId: "drive-1", files: [], filesComplete: true, folders: [] };
    qc.setQueryData(wikiKeys.driveTree("drive-1"), tree);

    injectNewFileIntoCaches(
      qc,
      makeFile({ id: "root-folder", mimeType: MIME_FOLDER, parentFolderId: "drive-1" }),
    );

    const folder = qc.getQueryData<DriveTree>(wikiKeys.driveTree("drive-1"))?.folders[0];
    expect(folder?.id).toBe("root-folder");
    expect(folder?.parentId).toBeUndefined();
  });

  it("splices root-level files into the drive tree's files list", () => {
    const qc = new QueryClient();
    const tree: DriveTree = { driveId: "drive-1", files: [], filesComplete: true, folders: [] };
    qc.setQueryData(wikiKeys.driveTree("drive-1"), tree);

    injectNewFileIntoCaches(
      qc,
      makeFile({ id: "rootdoc", mimeType: MIME_DOC, parentFolderId: "drive-1" }),
    );

    const updated = qc.getQueryData<DriveTree>(wikiKeys.driveTree("drive-1"));
    expect(updated?.files.map((f) => f.id)).toEqual(["rootdoc"]);
    expect(updated?.files[0]?.parentId).toBeUndefined();

    removeFileFromCaches(qc, "rootdoc", "drive-1", "drive-1");
    expect(qc.getQueryData<DriveTree>(wikiKeys.driveTree("drive-1"))?.files).toHaveLength(0);
  });
});

describe("findCachedNodeMeta", () => {
  it("finds a file in a folder-files listing", () => {
    const qc = new QueryClient();
    qc.setQueryData<DriveNode[]>(wikiKeys.folderFiles("folder-a"), [
      makeNode({ id: "doc-1", mimeType: MIME_DOC, name: "Roadmap" }),
    ]);

    expect(findCachedNodeMeta(qc, "doc-1")).toEqual({ mimeType: MIME_DOC, name: "Roadmap" });
  });

  it("includes the cached content version needed to mount HTML once", () => {
    const qc = new QueryClient();
    qc.setQueryData<DriveNode[]>(wikiKeys.folderFiles("folder-a"), [
      makeNode({
        id: "html-1",
        mimeType: "text/html",
        modifiedTime: "2026-08-22T12:00:00Z",
        name: "Published page",
      }),
    ]);

    expect(findCachedNodeMeta(qc, "html-1")).toEqual({
      mimeType: "text/html",
      modifiedTime: "2026-08-22T12:00:00Z",
      name: "Published page",
    });
  });

  it("finds a folder in a drive tree", () => {
    const qc = new QueryClient();
    const tree: DriveTree = {
      driveId: "drive-1",
      files: [],
      filesComplete: true,
      folders: [makeNode({ id: "folder-x", mimeType: MIME_FOLDER, name: "Specs" })],
    };
    qc.setQueryData(wikiKeys.driveTree("drive-1"), tree);

    expect(findCachedNodeMeta(qc, "folder-x")).toEqual({ mimeType: MIME_FOLDER, name: "Specs" });
  });

  it("resolves a shortcut by its target id with the target's mime type", () => {
    const qc = new QueryClient();
    qc.setQueryData<DriveNode[]>(wikiKeys.folderFiles("folder-a"), [
      makeNode({
        id: "shortcut-1",
        mimeType: MIME_SHORTCUT,
        name: "Budget (link)",
        shortcutTargetId: "sheet-9",
        shortcutTargetMimeType: MIME_SHEET,
      }),
    ]);

    expect(findCachedNodeMeta(qc, "sheet-9")).toEqual({
      mimeType: MIME_SHEET,
      name: "Budget (link)",
    });
    expect(findCachedNodeMeta(qc, "shortcut-1")).toBeNull();
  });

  it("does not mistake a shortcut timestamp for the target content version", () => {
    const qc = new QueryClient();
    qc.setQueryData<DriveNode[]>(wikiKeys.folderFiles("folder-a"), [
      makeNode({
        id: "shortcut-html",
        mimeType: MIME_SHORTCUT,
        modifiedTime: "shortcut-version",
        name: "Published page",
        shortcutTargetId: "html-target",
        shortcutTargetMimeType: "text/html",
      }),
    ]);

    expect(findCachedNodeMeta(qc, "html-target")).toEqual({
      mimeType: "text/html",
      name: "Published page",
    });
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
      files: [],
      filesComplete: true,
      folders: [makeNode({ id: "f1" }), makeNode({ id: "f2" })],
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

  it("can preserve active file metadata while moving between tree caches", () => {
    const qc = new QueryClient();
    const detail = { breadcrumb: [{ id: "drive-1", name: "My Drive" }], id: "x" };
    qc.setQueryData(wikiKeys.file("x"), detail);

    removeFileFromCaches(qc, "x", "folder-a", "drive-1", { removeFileQuery: false });

    expect(qc.getQueryData(wikiKeys.file("x"))).toEqual(detail);
  });
});

describe("optimisticMoveFile", () => {
  it("moves across drive caches and rolls both sides back", () => {
    const qc = new QueryClient();
    const node = makeNode({ id: "moving", name: "Moving" });
    qc.setQueryData<DriveTree>(wikiKeys.driveTree("drive-1"), {
      driveId: "drive-1",
      files: [node],
      filesComplete: true,
      folders: [],
    });
    qc.setQueryData<DriveTree>(wikiKeys.driveTree("drive-2"), {
      driveId: "drive-2",
      files: [],
      filesComplete: true,
      folders: [makeNode({ id: "folder-b", mimeType: MIME_FOLDER, name: "Folder B" })],
    });

    const rollback = optimisticMoveFile(
      qc,
      node,
      { driveId: "drive-2", parentFolderId: "folder-b" },
      { driveId: "drive-1", parentFolderId: undefined },
    );

    expect(qc.getQueryData<DriveTree>(wikiKeys.driveTree("drive-1"))?.files).toHaveLength(0);
    expect(qc.getQueryData<DriveTree>(wikiKeys.driveTree("drive-2"))?.files[0]).toMatchObject({
      id: "moving",
      parentId: "folder-b",
    });

    rollback();

    expect(qc.getQueryData<DriveTree>(wikiKeys.driveTree("drive-2"))?.files).toHaveLength(0);
    expect(qc.getQueryData<DriveTree>(wikiKeys.driveTree("drive-1"))?.files[0]).toMatchObject({
      id: "moving",
      parentId: undefined,
    });
  });
});

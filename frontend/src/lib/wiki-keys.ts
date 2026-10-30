const FOLDER_FILES_PREFIX = ["wiki", "folder-files"] as const;
const DRIVE_TREE_PREFIX = ["wiki", "drive-tree"] as const;
const DRIVE_SEARCH_PREFIX = ["wiki", "drive-search"] as const;

export const wikiKeys = {
  docIcons: ["wiki", "doc-icons"] as const,
  drives: ["wiki", "drives"] as const,
  // eslint-disable-next-line @sarj/no-positional-tuple-return -- TanStack Query requires query keys to be readonly arrays.
  driveSearch: (query: string) => [...DRIVE_SEARCH_PREFIX, query] as const,
  driveSearchSnippet: (fileId: string, query: string) =>
    // eslint-disable-next-line @sarj/no-positional-tuple-return -- TanStack Query requires query keys to be readonly arrays.
    [...DRIVE_SEARCH_PREFIX, "snippet", fileId, query] as const,
  // eslint-disable-next-line @sarj/no-positional-tuple-return -- TanStack Query requires query keys to be readonly arrays.
  driveTree: (driveId: string) => [...DRIVE_TREE_PREFIX, driveId] as const,
  driveTreePrefix: DRIVE_TREE_PREFIX,
  // eslint-disable-next-line @sarj/no-positional-tuple-return -- TanStack Query requires query keys to be readonly arrays.
  file: (fileId: string) => ["wiki", "file", fileId] as const,
  // eslint-disable-next-line @sarj/no-positional-tuple-return -- TanStack Query requires query keys to be readonly arrays.
  folderFiles: (folderId: string) => [...FOLDER_FILES_PREFIX, folderId] as const,
  folderFilesPrefix: FOLDER_FILES_PREFIX,
  folderIcons: ["wiki", "folder-icons"] as const,
  preferences: ["wiki", "preferences"] as const,
  sortOrders: ["wiki", "sort-orders"] as const,
};

export const authKeys = {
  me: ["auth", "me"] as const,
};

const FOLDER_FILES_PREFIX = ["wiki", "folder-files"] as const;
const DRIVE_TREE_PREFIX = ["wiki", "drive-tree"] as const;
const DRIVE_SEARCH_PREFIX = ["wiki", "drive-search"] as const;

export const wikiKeys = {
  drives: ["wiki", "drives"] as const,
  driveTreePrefix: DRIVE_TREE_PREFIX,
  driveTree: (driveId: string) => [...DRIVE_TREE_PREFIX, driveId] as const,
  folderFilesPrefix: FOLDER_FILES_PREFIX,
  folderFiles: (folderId: string) => [...FOLDER_FILES_PREFIX, folderId] as const,
  file: (fileId: string) => ["wiki", "file", fileId] as const,
  // v2: payload became { html, tabs } — the bump ignores persisted string-shaped entries
  docHtml: (fileId: string) => ["wiki", "doc-html", "v2", fileId] as const,
  docComments: (fileId: string) => ["wiki", "doc-comments", fileId] as const,
  preferences: ["wiki", "preferences"] as const,
  folderIcons: ["wiki", "folder-icons"] as const,
  docIcons: ["wiki", "doc-icons"] as const,
  sortOrders: ["wiki", "sort-orders"] as const,
  driveSearch: (query: string) => [...DRIVE_SEARCH_PREFIX, query] as const,
  driveSearchSnippet: (fileId: string, query: string) =>
    [...DRIVE_SEARCH_PREFIX, "snippet", fileId, query] as const,
};

export const authKeys = {
  me: ["auth", "me"] as const,
};

import { bootstrapRead } from "@/lib/wiki-schemas";

const WIRE_FIELDS = {
  avatarUrl: "avatar_url",
  coverUrl: "cover_url",
  displayName: "display_name",
  docIcons: "doc_icons",
  driveId: "drive_id",
  filesComplete: "files_complete",
  firstDriveTree: "first_drive_tree",
  folderDriveId: "folder_drive_id",
  folderIcons: "folder_icons",
  hiddenWorkspaceIds: "hidden_workspace_ids",
  mimeType: "mime_type",
  modifiedTime: "modified_time",
  parentId: "parent_id",
  showNonDocFiles: "show_non_doc_files",
  sortOrder: "sort_order",
  sortOrders: "sort_orders",
} as const;

const BOOTSTRAP_WIRE = {
  [WIRE_FIELDS.docIcons]: [
    { [WIRE_FIELDS.driveId]: "document", icon: "📖", [WIRE_FIELDS.coverUrl]: null },
  ],
  drives: [{ id: "drive", name: "Engineering" }],
  [WIRE_FIELDS.firstDriveTree]: {
    [WIRE_FIELDS.driveId]: "drive",
    files: [
      {
        id: "file",
        name: "Plan",
        [WIRE_FIELDS.mimeType]: "text/markdown",
        [WIRE_FIELDS.parentId]: null,
        [WIRE_FIELDS.modifiedTime]: null,
      },
    ],
    [WIRE_FIELDS.filesComplete]: true,
    folders: [],
  },
  [WIRE_FIELDS.folderIcons]: [{ [WIRE_FIELDS.folderDriveId]: "folder", icon: null }],
  me: {
    id: "user",
    email: "qa@sarj.ai",
    [WIRE_FIELDS.displayName]: null,
    [WIRE_FIELDS.avatarUrl]: null,
  },
  preferences: {
    [WIRE_FIELDS.hiddenWorkspaceIds]: ["hidden-drive"],
    [WIRE_FIELDS.showNonDocFiles]: false,
  },
  [WIRE_FIELDS.sortOrders]: [{ [WIRE_FIELDS.driveId]: "file", [WIRE_FIELDS.sortOrder]: 2 }],
};

describe("bootstrap wire validation and shared key transforms", () => {
  it("preserves nullable metadata and wire defaults in every bootstrap resource", () => {
    expect(bootstrapRead.parse(BOOTSTRAP_WIRE)).toEqual({
      docIcons: [{ driveId: "document", icon: "📖", coverUrl: undefined }],
      drives: [{ id: "drive", name: "Engineering", isPersonal: false }],
      firstDriveTree: {
        driveId: "drive",
        files: [
          {
            id: "file",
            name: "Plan",
            mimeType: "text/markdown",
            parentId: undefined,
            modifiedTime: undefined,
            shortcutInaccessible: false,
            shortcutTargetDriveId: undefined,
            shortcutTargetDriveName: undefined,
            shortcutTargetId: undefined,
            shortcutTargetMimeType: undefined,
          },
        ],
        filesComplete: true,
        folders: [],
      },
      folderIcons: [{ folderDriveId: "folder", icon: undefined }],
      me: { id: "user", email: "qa@sarj.ai", displayName: undefined, avatarUrl: undefined },
      preferences: { hiddenWorkspaceIds: ["hidden-drive"], showNonDocFiles: false },
      sortOrders: [{ driveId: "file", sortOrder: 2 }],
    });
  });

  it.each([
    {
      [WIRE_FIELDS.docIcons]: [
        { [WIRE_FIELDS.driveId]: 123, icon: null, [WIRE_FIELDS.coverUrl]: null },
      ],
    },
    { drives: [{ id: "drive", name: 123 }] },
    { [WIRE_FIELDS.folderIcons]: [{ [WIRE_FIELDS.folderDriveId]: null, icon: null }] },
    { me: { ...BOOTSTRAP_WIRE.me, email: "invalid-email" } },
    { preferences: { ...BOOTSTRAP_WIRE.preferences, [WIRE_FIELDS.hiddenWorkspaceIds]: [123] } },
    {
      [WIRE_FIELDS.sortOrders]: [
        { [WIRE_FIELDS.driveId]: "file", [WIRE_FIELDS.sortOrder]: "invalid" },
      ],
    },
  ])("rejects malformed nested metadata at the single wire-validation boundary: %j", (invalid) => {
    expect(bootstrapRead.safeParse({ ...BOOTSTRAP_WIRE, ...invalid }).success).toBe(false);
  });
});

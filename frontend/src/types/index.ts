export interface Page {
  id: string;
  parentId: string | null;
  googleDriveId: string;
  title: string;
  type: "page" | "section";
  icon: string | null;
  isPrivate: boolean;
  workspaceId: string | null;
  sortOrder: number;
  children: Page[];
  updatedAt?: string;
  lastEditedByEmail?: string;
  lastEditedByName?: string;
  createdBy?: string | null;
  createdByName?: string | null;
  createdByEmail?: string | null;
  createdByAvatar?: string | null;
}

export interface Workspace {
  id: string;
  name: string;
  googleGroupEmail: string | null;
  driveFolderId: string;
  icon: string | null;
  sortOrder: number;
}

export interface User {
  id: string;
  email: string;
  displayName: string | null;
  avatarUrl: string | null;
}

export type Permission = "view" | "edit";

export interface PagePermission {
  id: string;
  pageId: string;
  userId: string | null;
  workspaceId: string | null;
  permission: Permission;
  // Denormalized for display
  displayName: string;
  email?: string;
  avatarUrl?: string;
}

export interface UserSearchResult {
  id: string;
  email: string;
  displayName: string | null;
  avatarUrl: string | null;
}

export interface WorkspaceSearchResult {
  id: string;
  name: string;
  googleGroupEmail: string | null;
}

export interface SearchResult {
  id: string;
  title: string;
  icon: string | null;
  workspaceId: string | null;
  isPrivate: boolean;
  parentId: string | null;
  googleDriveId: string;
  snippet: string | null;
  rank: number;
}

export interface ImportFormat {
  id: string;
  name: string;
}

export interface ImportJob {
  id: string;
  status: "pending" | "processing" | "completed" | "failed";
  totalItems: number;
  processedItems: number;
  errorMessage: string | null;
  createdAt: string;
  completedAt: string | null;
}

// --- Document content types (from Google Docs API) ---

export interface TextRun {
  text?: string;
  bold?: boolean;
  italic?: boolean;
  underline?: boolean;
  strikethrough?: boolean;
  smallCaps?: boolean;
  link?: string;
  superscript?: boolean;
  subscript?: boolean;
  color?: string;
  backgroundColor?: string;
  fontFamily?: string;
  fontSize?: number;
  type?:
    | "image"
    | "person"
    | "rich_link"
    | "footnote_ref"
    | "equation"
    | "auto_text"
    | "drawing"
    | "chart"
    | "chip_placeholder"
    | "date"
    | "code";
  url?: string;
  alt?: string;
  width?: number;
  height?: number;
  email?: string;
  mimeType?: string;
  footnoteId?: string;
  number?: string;
}

export interface CellParagraph {
  runs: TextRun[];
  alignment?: "center" | "right" | "justify";
}

export interface TableCellData {
  content: CellParagraph[];
  rowSpan?: number;
  colSpan?: number;
  backgroundColor?: string;
  verticalAlign?: string;
}

export interface ListItem {
  runs: TextRun[];
  nesting: number;
}

export interface TocItem {
  text: string;
  level: number;
  link?: string;
}

export interface BlockStyle {
  alignment?: "center" | "right" | "justify";
  direction?: "rtl";
  backgroundColor?: string;
  indentStart?: number;
}

/** Known block types rendered by DocViewer. API may return other types (handled as no-op). */
export type ContentBlockType =
  | "heading"
  | "paragraph"
  | "list"
  | "table"
  | "image"
  | "table_of_contents"
  | "horizontal_rule"
  | "code_block"
  | (string & {});

export interface DocTab {
  title: string;
  blocks: ContentBlock[];
  tabId?: string;
  depth?: number;
}

export interface PageContent {
  tabs: DocTab[];
  cached: boolean;
}

export interface ContentBlock {
  type: ContentBlockType;
  level?: number;
  runs?: TextRun[];
  ordered?: boolean;
  checklist?: boolean;
  items?: ListItem[];
  rows?: TableCellData[][];
  hasHeader?: boolean;
  hasChips?: boolean;
  caption?: string;
  columnWidths?: (number | null)[];
  style?: BlockStyle;
  variant?: "title" | "subtitle";
  url?: string;
  alt?: string;
  width?: number;
  height?: number;
  headingId?: string;
  tocItems?: TocItem[];
  code?: string;
}

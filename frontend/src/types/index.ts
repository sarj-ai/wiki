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
}

export interface Workspace {
  id: string;
  name: string;
  googleGroupEmail: string;
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
    | "date";
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

export interface ContentBlock {
  type: string;
  level?: number;
  runs?: TextRun[];
  ordered?: boolean;
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
  tocItems?: TocItem[];
}

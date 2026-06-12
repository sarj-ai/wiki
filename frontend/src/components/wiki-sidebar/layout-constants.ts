// Width of the absolute-positioned icon column in pixels, including
// the gap between it and the row text. Shared by file and folder rows
// so they keep the same horizontal rhythm at every depth.
export const ICON_COLUMN_WIDTH = 24;

// Left padding of a row at a given nesting depth, in pixels. One unit
// of indentation per level on top of a base inset shared by every row.
export function rowIndent(depth: number): number {
  return depth * DEPTH_INDENT_UNIT + BASE_INDENT;
}

const DEPTH_INDENT_UNIT = 12;
const BASE_INDENT = 8;

// Horizontal offset of a folder row's icon past its chevron:
// chevron width (~14px) + gap. Distinct from ICON_COLUMN_WIDTH.
export const CHEVRON_OFFSET = 18;

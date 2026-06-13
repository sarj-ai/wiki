const DEPTH_INDENT_UNIT = 12;
const BASE_INDENT = 8;

export const ICON_COLUMN_WIDTH = 24;

export function rowIndent(depth: number): number {
  return depth * DEPTH_INDENT_UNIT + BASE_INDENT;
}

export const CHEVRON_OFFSET = 18;

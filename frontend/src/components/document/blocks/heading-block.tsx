import type { TextRun, BlockStyle } from "@/types";
import { RunRenderer } from "./run-renderer";
import { buildBlockCss } from "./utils";

export function HeadingBlock({
  level,
  runs,
  variant,
  style,
}: {
  level: number;
  runs: TextRun[];
  variant?: string;
  style?: BlockStyle;
}) {
  const Tag = `h${Math.min(level, 6)}` as
    | "h1"
    | "h2"
    | "h3"
    | "h4"
    | "h5"
    | "h6";

  const classes: Record<string, string> = {
    title: "doc-title",
    subtitle: "doc-subtitle",
  };

  const headingClasses: Record<number, string> = {
    1: "doc-h1",
    2: "doc-h2",
    3: "doc-h3",
  };

  const headingClass =
    (variant && classes[variant]) ||
    headingClasses[level] ||
    "";

  return (
    <Tag className={headingClass || undefined} style={buildBlockCss(style)}>
      <RunRenderer runs={runs} />
    </Tag>
  );
}

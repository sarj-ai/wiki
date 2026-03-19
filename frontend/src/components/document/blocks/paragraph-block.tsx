import type { TextRun, BlockStyle } from "@/types";
import { RunRenderer } from "./run-renderer";
import { buildBlockCss } from "./utils";

export function ParagraphBlock({
  runs,
  style,
}: {
  runs: TextRun[];
  style?: BlockStyle;
}) {
  if (!runs || runs.length === 0) {
    return <div className="h-3" aria-hidden />;
  }

  const hasImages = runs.some((r) => r.type === "image");
  if (hasImages && runs.every((r) => r.type === "image")) {
    return (
      <figure className="my-8" style={buildBlockCss(style)}>
        <RunRenderer runs={runs} />
      </figure>
    );
  }

  // Use <div> when mixing images with text to avoid invalid <img> inside <p>
  const Tag = hasImages ? "div" : "p";
  return (
    <Tag style={buildBlockCss(style)}>
      <RunRenderer runs={runs} />
    </Tag>
  );
}

import type { ContentBlock } from "@/types";
import { HeadingBlock } from "./heading-block";
import { ParagraphBlock } from "./paragraph-block";
import { ListBlock } from "./list-block";
import { DocTableBlock } from "./table-block";
import { ImageBlock } from "./image-block";
import { CodeBlockComponent } from "./code-block";
import { TocBlock } from "./toc-block";
import { HorizontalRule } from "./horizontal-rule";

export function BlockRenderer({ block }: { block: ContentBlock }) {
  switch (block.type) {
    case "heading":
      return (
        <HeadingBlock
          level={block.level || 1}
          runs={block.runs || []}
          variant={block.variant}
          style={block.style}
        />
      );
    case "paragraph":
      return <ParagraphBlock runs={block.runs || []} style={block.style} />;
    case "list":
      return (
        <ListBlock
          ordered={block.ordered || false}
          items={block.items || []}
        />
      );
    case "table":
      return (
        <DocTableBlock
          rows={block.rows || []}
          hasHeader={block.hasHeader}
          hasChips={block.hasChips}
          caption={block.caption}
        />
      );
    case "image":
      return (
        <ImageBlock
          url={block.url || ""}
          alt={block.alt}
          width={block.width}
        />
      );
    case "code_block":
      return <CodeBlockComponent code={block.code || ""} />;
    case "table_of_contents":
      if (!block.tocItems || block.tocItems.length === 0) return null;
      return <TocBlock items={block.tocItems} />;
    case "horizontal_rule":
      return <HorizontalRule />;
    default:
      return null;
  }
}

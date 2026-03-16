import type { ListItem } from "@/types";
import { RunRenderer } from "./run-renderer";

export function ListBlock({
  ordered,
  items,
}: {
  ordered: boolean;
  items: ListItem[];
}) {
  const Tag = ordered ? "ol" : "ul";
  return (
    <Tag className={ordered ? "doc-ol" : "doc-ul"}>
      {items.map((item, i) => (
        <li
          key={i}
          style={
            item.nesting > 0
              ? { marginInlineStart: `${item.nesting * 1.5}em` }
              : undefined
          }
        >
          <RunRenderer runs={item.runs} />
        </li>
      ))}
    </Tag>
  );
}

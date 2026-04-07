import type { ListItem } from "@/types";
import { RunRenderer } from "./run-renderer";

export function ListBlock({
  ordered,
  items,
  checklist,
}: {
  ordered: boolean;
  items: ListItem[];
  checklist?: boolean;
}) {
  if (checklist) {
    return (
      <ul className="doc-checklist">
        {items.map((item, i) => (
          <li
            key={i}
            style={
              typeof item.nesting === "number" && item.nesting > 0
                ? { marginInlineStart: `${item.nesting * 1.5}em` }
                : undefined
            }
          >
            <span className="doc-checklist-box" aria-hidden="true" />
            <span className="doc-checklist-content">
              <RunRenderer runs={item.runs} />
            </span>
          </li>
        ))}
      </ul>
    );
  }

  const Tag = ordered ? "ol" : "ul";
  return (
    <Tag className={ordered ? "doc-ol" : "doc-ul"}>
      {items.map((item, i) => (
        <li
          key={i}
          data-nesting={item.nesting || 0}
          style={
            typeof item.nesting === "number" && item.nesting > 0
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

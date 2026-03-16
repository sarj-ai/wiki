import { List } from "lucide-react";
import type { TocItem } from "@/types";
import { sanitizeUrl } from "./utils";

export function TocBlock({ items }: { items: TocItem[] }) {
  return (
    <nav className="doc-toc">
      <div className="doc-toc-header">
        <List className="size-4" />
        <span>Table of Contents</span>
      </div>
      <ul className="doc-toc-list">
        {items.map((item, i) => (
          <li
            key={i}
            style={{ paddingInlineStart: `${item.level * 1.25}rem` }}
          >
            {item.link ? (
              <a href={sanitizeUrl(item.link) ?? "#"} className="doc-toc-link">
                {item.text}
              </a>
            ) : (
              <span className="doc-toc-text">{item.text}</span>
            )}
          </li>
        ))}
      </ul>
    </nav>
  );
}

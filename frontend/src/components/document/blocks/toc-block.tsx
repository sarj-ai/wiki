// @ts-nocheck — scheduled for deletion in Sub-PR D of Navigator pivot
import { List } from "lucide-react";
import type { TocItem } from "@/types";

/**
 * Google Docs TOC links look like "#heading=h.abc123".
 * Extract the heading ID so we can scroll to it locally.
 */
function toAnchorId(link: string): string | null {
  const match = link.match(/^#heading=(.+)$/);
  if (!match) return null;
  // Sanitize to match heading-block.tsx's id sanitization
  return match[1].replace(/[^a-zA-Z0-9_-]/g, "") || null;
}

export function TocBlock({ items }: { items: TocItem[] }) {
  function handleClick(e: React.MouseEvent<HTMLAnchorElement>, id: string) {
    e.preventDefault();
    const el = document.getElementById(id);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  return (
    <nav className="doc-toc">
      <div className="doc-toc-header">
        <List className="size-4" />
        <span>Table of Contents</span>
      </div>
      <ul className="doc-toc-list">
        {items.map((item, i) => {
          const anchorId = item.link ? toAnchorId(item.link) : null;
          return (
            <li
              key={i}
              style={
                typeof item.level === "number" && item.level > 0
                  ? { paddingInlineStart: `${item.level * 1.25}rem` }
                  : undefined
              }
            >
              {anchorId ? (
                <a
                  href={`#${anchorId}`}
                  onClick={(e) => handleClick(e, anchorId)}
                  className="doc-toc-link"
                >
                  {item.text}
                </a>
              ) : (
                <span className="doc-toc-text">{item.text}</span>
              )}
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

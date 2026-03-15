"use client";

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { codeToHtml } from "shiki";
import {
  CalendarDays,
  Check,
  Copy,
  ExternalLink,
  Hash,
  ImageIcon,
  LayoutGrid,
  List,
  Pencil,
  User,
} from "lucide-react";
import type {
  TextRun,
  TableCellData,
  ListItem,
  TocItem,
  BlockStyle,
  ContentBlock,
  DocTab,
} from "@/types";

/**
 * Returns the URL if it uses a safe protocol, or null otherwise.
 * Prevents javascript: and data: protocol injection from untrusted content.
 */
function sanitizeUrl(url: string | undefined): string | undefined {
  if (!url) return undefined;
  try {
    const parsed = new URL(url, window.location.origin);
    if (parsed.protocol === "http:" || parsed.protocol === "https:" || parsed.protocol === "mailto:") {
      return url;
    }
    return undefined;
  } catch {
    // Relative URLs (e.g. #heading-id) are safe
    if (url.startsWith("#") || url.startsWith("/")) {
      return url;
    }
    return undefined;
  }
}

const STATUS_CLASSES: Record<string, string> = {
  "not started": "doc-status-neutral",
  "in progress": "doc-status-active",
  "in review": "doc-status-active",
  "blocked": "doc-status-blocked",
  "approved": "doc-status-success",
  "launched": "doc-status-success",
  "completed": "doc-status-success",
  "done": "doc-status-success",
  "on hold": "doc-status-warning",
  "paused": "doc-status-warning",
  "cancelled": "doc-status-blocked",
  "canceled": "doc-status-blocked",
  "pending": "doc-status-warning",
  "draft": "doc-status-neutral",
};

function getStatusClass(text: string): string | null {
  return STATUS_CLASSES[text.trim().toLowerCase()] ?? null;
}

interface DocViewerProps {
  tabs: DocTab[];
}

// ---------------------------------------------------------------------------
// Run renderer
// ---------------------------------------------------------------------------

function RunRenderer({ runs }: { runs: TextRun[] }) {
  return (
    <>
      {runs.map((run, i) => {
        // ── Images ──
        if (run.type === "image" && run.url) {
          const safeSrc = sanitizeUrl(run.url);
          if (!safeSrc) return <Fragment key={i} />;
          return (
            <img
              key={i}
              src={safeSrc}
              alt={run.alt || ""}
              className="my-6 max-w-full rounded-xl shadow-sm ring-1 ring-black/[0.04] dark:ring-white/[0.06] inline-block"
              style={run.width ? { maxWidth: `${run.width}pt` } : undefined}
            />
          );
        }

        // ── Person mention ──
        if (run.type === "person") {
          return (
            <span
              key={i}
              className="doc-chip-person"
              title={run.email}
            >
              <User className="size-3 shrink-0" />
              <span>{run.text}</span>
            </span>
          );
        }

        // ── Rich link ──
        if (run.type === "rich_link") {
          const safeHref = sanitizeUrl(run.link);
          return (
            <a
              key={i}
              href={safeHref ?? "#"}
              target="_blank"
              rel="noopener noreferrer"
              className="doc-chip-link"
            >
              <ExternalLink className="size-3 shrink-0" />
              <span>{run.text}</span>
            </a>
          );
        }

        // ── Footnote reference ──
        if (run.type === "footnote_ref") {
          const safeId = run.footnoteId?.replace(/[^a-zA-Z0-9_-]/g, "") ?? "";
          return (
            <sup key={i} className="doc-footnote-ref">
              <a href={`#fn-${safeId}`}>
                {run.number}
              </a>
            </sup>
          );
        }

        // ── Smart chip placeholder (fallback when HTML export can't resolve) ──
        if (run.type === "chip_placeholder") {
          return (
            <span key={i} className="doc-chip-placeholder">
              <LayoutGrid className="size-3 shrink-0" />
              <span>Smart chip</span>
            </span>
          );
        }

        // ── Date ──
        if (run.type === "date") {
          return (
            <span key={i} className="doc-chip-date">
              <CalendarDays className="size-3 shrink-0" />
              <span>{run.text}</span>
            </span>
          );
        }

        // ── Equation ──
        if (run.type === "equation") {
          return (
            <code key={i} className="doc-equation">
              {run.text}
            </code>
          );
        }

        // ── Auto text ──
        if (run.type === "auto_text") {
          return (
            <span key={i} className="text-muted-foreground text-sm italic">
              {run.text}
            </span>
          );
        }

        // ── Drawing ──
        if (run.type === "drawing") {
          return (
            <span key={i} className="doc-chip-placeholder">
              <ImageIcon className="size-3 shrink-0" />
              <span>{run.text}</span>
            </span>
          );
        }

        // ── Chart ──
        if (run.type === "chart") {
          return (
            <span key={i} className="doc-chip-placeholder">
              <Hash className="size-3 shrink-0" />
              <span>{run.text}</span>
            </span>
          );
        }

        // ── Inline code ──
        if (run.type === "code") {
          return (
            <code key={i} className="doc-inline-code">
              {run.text}
            </code>
          );
        }

        // ── Regular text run ──
        let rendered: React.ReactNode = run.text || "";

        if (run.bold) rendered = <strong>{rendered}</strong>;
        if (run.italic) rendered = <em>{rendered}</em>;
        if (run.underline) rendered = <u className="decoration-foreground/30 underline-offset-[3px]">{rendered}</u>;
        if (run.strikethrough) rendered = <s className="decoration-muted-foreground/60">{rendered}</s>;
        if (run.smallCaps)
          rendered = <span style={{ fontVariant: "small-caps" }}>{rendered}</span>;
        if (run.superscript) rendered = <sup>{rendered}</sup>;
        if (run.subscript) rendered = <sub>{rendered}</sub>;

        // Inline styles (color, background, font)
        const inlineStyle: React.CSSProperties = {};
        if (run.color) inlineStyle.color = run.color;
        if (run.backgroundColor) inlineStyle.backgroundColor = run.backgroundColor;
        if (run.fontFamily) inlineStyle.fontFamily = run.fontFamily;
        if (run.fontSize) inlineStyle.fontSize = `${run.fontSize}pt`;

        const hasInlineStyle = Object.keys(inlineStyle).length > 0;

        if (run.link) {
          const safeHref = sanitizeUrl(run.link);
          return (
            <a
              key={i}
              href={safeHref ?? "#"}
              target="_blank"
              rel="noopener noreferrer"
              className="doc-link"
              style={hasInlineStyle ? inlineStyle : undefined}
            >
              {rendered}
            </a>
          );
        }

        if (hasInlineStyle) {
          return (
            <span key={i} style={inlineStyle}>
              {rendered}
            </span>
          );
        }

        return <Fragment key={i}>{rendered}</Fragment>;
      })}
    </>
  );
}

// ---------------------------------------------------------------------------
// Block style helper
// ---------------------------------------------------------------------------

function buildBlockCss(style?: BlockStyle): React.CSSProperties | undefined {
  if (!style) return undefined;
  const css: React.CSSProperties = {};
  if (style.alignment) css.textAlign = style.alignment;
  if (style.direction) css.direction = style.direction;
  if (style.backgroundColor) css.backgroundColor = style.backgroundColor;
  if (style.indentStart) css.paddingInlineStart = `${style.indentStart}pt`;
  return Object.keys(css).length > 0 ? css : undefined;
}

// ---------------------------------------------------------------------------
// Block renderers
// ---------------------------------------------------------------------------

function HeadingBlock({
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

function ParagraphBlock({
  runs,
  style,
}: {
  runs: TextRun[];
  style?: BlockStyle;
}) {
  if (!runs || runs.length === 0) {
    return <div className="h-4" aria-hidden />;
  }

  const hasOnlyImages = runs.every((r) => r.type === "image");
  if (hasOnlyImages) {
    return (
      <figure className="my-8" style={buildBlockCss(style)}>
        <RunRenderer runs={runs} />
      </figure>
    );
  }

  return (
    <p style={buildBlockCss(style)}>
      <RunRenderer runs={runs} />
    </p>
  );
}

function ListBlock({
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

function DocTableBlock({
  rows,
  hasHeader,
  hasChips,
  caption,
}: {
  rows: TableCellData[][];
  hasHeader?: boolean;
  hasChips?: boolean;
  caption?: string;
}) {
  if (!rows || rows.length === 0) return null;

  // Track which cells to skip due to rowSpan/colSpan
  const skipMap = new Map<string, boolean>();
  rows.forEach((row, rowIndex) => {
    let colIndex = 0;
    row.forEach((cell) => {
      while (skipMap.has(`${rowIndex}:${colIndex}`)) colIndex++;
      const rowSpanCount = cell.rowSpan || 1;
      const colSpanCount = cell.colSpan || 1;
      for (let rowOffset = 0; rowOffset < rowSpanCount; rowOffset++) {
        for (let colOffset = 0; colOffset < colSpanCount; colOffset++) {
          if (rowOffset !== 0 || colOffset !== 0) {
            skipMap.set(`${rowIndex + rowOffset}:${colIndex + colOffset}`, true);
          }
        }
      }
      colIndex += colSpanCount;
    });
  });

  const headerRow = hasHeader ? rows[0] : null;
  const bodyRows = hasHeader ? rows.slice(1) : rows;

  // Detect section rows (single cell spanning all columns = group header)
  const numCols = rows.length > 0
    ? Math.max(...rows.map(row => row.reduce((sum, cell) => sum + (cell.colSpan || 1), 0)))
    : 0;

  function isSectionRow(row: TableCellData[]): boolean {
    if (numCols <= 1) return false;

    // Case 1: Single cell with colSpan spanning all columns (merged cells)
    if (row.length === 1 && (row[0].colSpan || 1) >= numCols) return true;

    // Case 2: Only the first cell has content, rest are empty, and text is bold
    if (row.length > 1) {
      const firstHasContent = row[0]?.content.some((para) =>
        para.runs.some((run) => run.text?.trim()),
      );
      const restEmpty = row.slice(1).every(
        (cell) =>
          !cell.content.some((para) =>
            para.runs.some((run) => run.text?.trim()),
          ),
      );
      if (firstHasContent && restEmpty) {
        const firstRuns = row[0].content.flatMap((p) => p.runs);
        const allBold = firstRuns.every(
          (run) => run.bold || !run.text?.trim(),
        );
        const text = firstRuns
          .map((r) => r.text || "")
          .join("")
          .trim();
        return allBold && text.length > 0 && text.length < 50;
      }
    }

    return false;
  }

  function renderCell(
    cell: TableCellData,
    isHeader: boolean,
  ) {
    const Tag = isHeader ? "th" : "td";
    const cellStyle: React.CSSProperties = {};
    if (cell.verticalAlign) cellStyle.verticalAlign = cell.verticalAlign;

    // Detect single status value (e.g. "In progress", "Blocked")
    const singleRun =
      !isHeader &&
      cell.content.length === 1 &&
      cell.content[0].runs.length === 1 &&
      cell.content[0].runs[0].text &&
      !cell.content[0].runs[0].type &&
      !cell.content[0].runs[0].link
        ? cell.content[0].runs[0]
        : null;
    const statusClass = singleRun ? getStatusClass(singleRun.text!) : null;

    return (
      <Tag
        rowSpan={cell.rowSpan && cell.rowSpan > 1 ? cell.rowSpan : undefined}
        colSpan={cell.colSpan && cell.colSpan > 1 ? cell.colSpan : undefined}
        style={Object.keys(cellStyle).length > 0 ? cellStyle : undefined}
        className={isHeader ? "doc-table-th" : "doc-table-td"}
      >
        {statusClass ? (
          <span className={statusClass}>{singleRun!.text!.trim()}</span>
        ) : (
          cell.content.map((para, pi) => {
            const paragraphStyle: React.CSSProperties = {};
            if (para.alignment) paragraphStyle.textAlign = para.alignment;
            return (
              <div
                key={pi}
                style={Object.keys(paragraphStyle).length > 0 ? paragraphStyle : undefined}
                className={pi > 0 ? "mt-1.5" : undefined}
              >
                <RunRenderer runs={para.runs} />
              </div>
            );
          })
        )}
      </Tag>
    );
  }

  return (
    <div className="doc-table-wrapper not-prose">
      {caption && <div className="doc-table-caption">{caption}</div>}
      <div className="doc-table-container">
        <table className="doc-table">
          {headerRow && (
            <thead>
              <tr>
                {headerRow.map((cell, ci) => (
                  <Fragment key={ci}>{renderCell(cell, true)}</Fragment>
                ))}
              </tr>
            </thead>
          )}
          <tbody>
            {bodyRows.map((row, bodyRowIdx) => {
              const absoluteRowIdx = hasHeader ? bodyRowIdx + 1 : bodyRowIdx;
              const sectionRow = isSectionRow(row);

              // Section rows: single cell spanning all columns
              if (sectionRow) {
                const firstCell = row[0];
                return (
                  <tr key={bodyRowIdx} className="doc-table-row doc-table-section-row">
                    <td colSpan={numCols} className="doc-table-td">
                      {firstCell.content.map((para, pi) => (
                        <div key={pi} className={pi > 0 ? "mt-1.5" : undefined}>
                          <RunRenderer runs={para.runs} />
                        </div>
                      ))}
                    </td>
                  </tr>
                );
              }

              // Regular rows: render cells + pad missing columns
              let colIndex = 0;
              const visibleCells: { cell: TableCellData; key: number }[] = [];
              for (let cellIdx = 0; cellIdx < row.length; cellIdx++) {
                while (skipMap.has(`${absoluteRowIdx}:${colIndex}`)) colIndex++;
                visibleCells.push({ cell: row[cellIdx], key: cellIdx });
                const colSpanCount = row[cellIdx].colSpan || 1;
                colIndex += colSpanCount;
              }

              // Count remaining unfilled columns (not covered by rowSpan)
              let paddingCols = 0;
              for (let col = colIndex; col < numCols; col++) {
                if (!skipMap.has(`${absoluteRowIdx}:${col}`)) paddingCols++;
              }

              return (
                <tr key={bodyRowIdx} className="doc-table-row">
                  {visibleCells.map(({ cell, key }) => (
                    <Fragment key={key}>{renderCell(cell, false)}</Fragment>
                  ))}
                  {paddingCols > 0 &&
                    Array.from({ length: paddingCols }).map((_, pi) => (
                      <td key={`pad-${pi}`} className="doc-table-td" />
                    ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {hasChips && (
        <div className="doc-table-chip-notice">
          <Pencil className="size-3 shrink-0" />
          <span>
            Some values use smart chips.{" "}
            <button
              type="button"
              className="underline underline-offset-2 hover:text-foreground transition-colors"
              onClick={() => {
                const editBtn =
                  document.querySelector<HTMLButtonElement>(
                    "[data-edit-toggle]",
                  );
                editBtn?.click();
              }}
            >
              Edit mode
            </button>{" "}
            shows all data.
          </span>
        </div>
      )}
    </div>
  );
}

function ImageBlock({
  url,
  alt,
  width,
}: {
  url: string;
  alt?: string;
  width?: number;
}) {
  const safeSrc = sanitizeUrl(url);
  if (!safeSrc) return null;

  return (
    <figure className="my-8">
      <img
        src={safeSrc}
        alt={alt || ""}
        className="max-w-full rounded-xl shadow-sm ring-1 ring-black/[0.04] dark:ring-white/[0.06]"
        style={width ? { maxWidth: `${width}pt` } : undefined}
      />
    </figure>
  );
}

function TocBlock({ items }: { items: TocItem[] }) {
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

function guessLanguage(code: string): string {
  const first = code.trimStart().slice(0, 200);
  if (/^(import |export |const |let |var |function |class |=>|async )/.test(first)) return "typescript";
  if (/^(def |class |import |from |if __name__|print\()/.test(first)) return "python";
  if (/^(SELECT |INSERT |UPDATE |DELETE |CREATE |ALTER |DROP |"SELECT|"INSERT|"UPDATE|"DELETE)/i.test(first)) return "sql";
  if (/^(package |func |import \(|type |var )/.test(first)) return "go";
  if (/^(<\?php|namespace |use |public function)/.test(first)) return "php";
  if (/^(#include|int main|void |printf\()/.test(first)) return "c";
  if (/^(\$ |# |sudo |npm |yarn |pip |curl |wget |cd |ls |mkdir |echo |cat )/.test(first)) return "bash";
  if (/^\{[\s\n]*("|')/.test(first)) return "json";
  if (/^(<html|<div|<span|<!DOCTYPE)/i.test(first)) return "html";
  if (/^(apiVersion:|kind:|metadata:)/.test(first)) return "yaml";
  if (/^(FROM |RUN |CMD |COPY |EXPOSE |ENTRYPOINT )/i.test(first)) return "dockerfile";
  return "text";
}

function CodeBlockComponent({ code }: { code: string }) {
  const [copied, setCopied] = useState(false);
  const [highlightedHtml, setHighlightedHtml] = useState<string | null>(null);
  const lang = useMemo(() => guessLanguage(code), [code]);

  useEffect(() => {
    let cancelled = false;
    codeToHtml(code, {
      lang,
      theme: "github-dark-default",
    }).then((html) => {
      if (!cancelled) setHighlightedHtml(html);
    }).catch(() => {
      // Fallback to plain text if language not supported
      if (!cancelled) setHighlightedHtml(null);
    });
    return () => { cancelled = true; };
  }, [code, lang]);

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(code).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }, [code]);

  return (
    <div className="doc-code-block">
      <button
        type="button"
        onClick={handleCopy}
        className="doc-code-copy"
        aria-label="Copy code"
      >
        {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
      </button>
      {highlightedHtml ? (
        <div
          className="doc-code-shiki"
          dangerouslySetInnerHTML={{ __html: highlightedHtml }}
        />
      ) : (
        <pre><code>{code}</code></pre>
      )}
    </div>
  );
}

function HorizontalRule() {
  return (
    <div className="doc-hr" aria-hidden>
      <div className="doc-hr-line" />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Block dispatcher
// ---------------------------------------------------------------------------

function BlockRenderer({ block }: { block: ContentBlock }) {
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
      return <TocBlock items={block.tocItems ?? []} />;
    case "horizontal_rule":
      return <HorizontalRule />;
    default:
      return null;
  }
}

// ---------------------------------------------------------------------------
// Tab divider
// ---------------------------------------------------------------------------

function TabDivider({ title, id }: { title: string; id: string }) {
  return (
    <div id={id} className="doc-tab-divider" data-tab-divider>
      <div className="doc-tab-divider-line" />
      <span className="doc-tab-divider-label">{title}</span>
      <div className="doc-tab-divider-line" />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Scroll-spy tab nav
// ---------------------------------------------------------------------------

function TabScrollSpy({
  tabs,
  activeIndex,
  onClickTab,
}: {
  tabs: DocTab[];
  activeIndex: number;
  onClickTab: (id: string) => void;
}) {
  return (
    <nav className="doc-tab-nav">
      {tabs.map((tab, i) => (
        <button
          key={i}
          type="button"
          onClick={() => onClickTab(`doc-tab-${i}`)}
          className={`doc-tab-nav-item ${i === activeIndex ? "doc-tab-nav-active" : ""}`}
        >
          <span className="doc-tab-nav-dot" />
          <span className="doc-tab-nav-text">{tab.title}</span>
        </button>
      ))}
    </nav>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function DocViewer({ tabs }: DocViewerProps) {
  const isMultiTab = tabs.length > 1;
  const [activeTab, setActiveTab] = useState(0);
  const tabRefs = useRef<Map<string, HTMLElement>>(new Map());

  // Scroll-spy: observe which tab divider is at the top of the viewport
  useEffect(() => {
    if (!isMultiTab) return;

    const observer = new IntersectionObserver(
      (entries) => {
        // Find the topmost visible tab divider
        for (const entry of entries) {
          if (entry.isIntersecting) {
            const idx = Number(entry.target.id.replace("doc-tab-", ""));
            if (!Number.isNaN(idx)) setActiveTab(idx);
          }
        }
      },
      { rootMargin: "-10% 0px -80% 0px" },
    );

    // Slight delay to let DOM settle
    const timer = setTimeout(() => {
      document.querySelectorAll("[data-tab-divider]").forEach((el) => {
        observer.observe(el);
      });
    }, 100);

    return () => {
      clearTimeout(timer);
      observer.disconnect();
    };
  }, [isMultiTab, tabs]);

  const scrollToTab = useCallback((id: string) => {
    document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

  // Single tab — render blocks directly, no dividers
  if (!isMultiTab) {
    const blocks = tabs[0]?.blocks ?? [];
    return (
      <article className="doc-viewer">
        {blocks.map((block, i) => (
          <BlockRenderer key={i} block={block} />
        ))}
      </article>
    );
  }

  return (
    <div className="doc-tabbed-layout">
      <TabScrollSpy tabs={tabs} activeIndex={activeTab} onClickTab={scrollToTab} />
      <article className="doc-viewer">
        {tabs.map((tab, tabIdx) => (
          <Fragment key={tabIdx}>
            <TabDivider title={tab.title} id={`doc-tab-${tabIdx}`} />
            {tab.blocks.map((block, blockIdx) => (
              <BlockRenderer key={`${tabIdx}-${blockIdx}`} block={block} />
            ))}
          </Fragment>
        ))}
      </article>
    </div>
  );
}

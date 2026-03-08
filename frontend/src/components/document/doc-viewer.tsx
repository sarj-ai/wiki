"use client";

import { Fragment } from "react";
import {
  CalendarDays,
  ExternalLink,
  FileText,
  Hash,
  ImageIcon,
  LayoutGrid,
  List,
  Pencil,
  User,
} from "lucide-react";

// ---------------------------------------------------------------------------
// Status badge detection for table cells
// ---------------------------------------------------------------------------

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
  return STATUS_CLASSES[text.trim().toLowerCase()] || null;
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface TextRun {
  text?: string;
  bold?: boolean;
  italic?: boolean;
  underline?: boolean;
  strikethrough?: boolean;
  smallCaps?: boolean;
  link?: string;
  superscript?: boolean;
  subscript?: boolean;
  color?: string;
  backgroundColor?: string;
  fontFamily?: string;
  fontSize?: number;
  type?:
    | "image"
    | "person"
    | "rich_link"
    | "footnote_ref"
    | "equation"
    | "auto_text"
    | "drawing"
    | "chart"
    | "chip_placeholder"
    | "date";
  url?: string;
  alt?: string;
  width?: number;
  height?: number;
  email?: string;
  mimeType?: string;
  footnoteId?: string;
  number?: string;
}

interface CellParagraph {
  runs: TextRun[];
  alignment?: "center" | "right" | "justify";
}

interface TableCellData {
  content: CellParagraph[];
  rowSpan?: number;
  colSpan?: number;
  backgroundColor?: string;
  verticalAlign?: string;
}

interface ListItem {
  runs: TextRun[];
  nesting: number;
}

interface TocItem {
  text: string;
  level: number;
  link?: string;
}

interface BlockStyle {
  alignment?: "center" | "right" | "justify";
  direction?: "rtl";
  backgroundColor?: string;
  indentStart?: number;
}

interface ContentBlock {
  type: string;
  level?: number;
  runs?: TextRun[];
  ordered?: boolean;
  items?: ListItem[];
  rows?: TableCellData[][];
  hasHeader?: boolean;
  hasChips?: boolean;
  caption?: string;
  columnWidths?: (number | null)[];
  style?: BlockStyle;
  variant?: "title" | "subtitle";
  url?: string;
  alt?: string;
  width?: number;
  height?: number;
  tocItems?: TocItem[];
}

interface DocViewerProps {
  blocks: ContentBlock[];
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
          return (
            <img
              key={i}
              src={run.url}
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
          return (
            <a
              key={i}
              href={run.link}
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
          return (
            <sup key={i} className="doc-footnote-ref">
              <a href={`#fn-${run.footnoteId}`}>
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

        // ── Regular text run ──
        let el: React.ReactNode = run.text || "";

        if (run.bold) el = <strong>{el}</strong>;
        if (run.italic) el = <em>{el}</em>;
        if (run.underline) el = <u className="decoration-foreground/30 underline-offset-[3px]">{el}</u>;
        if (run.strikethrough) el = <s className="decoration-muted-foreground/60">{el}</s>;
        if (run.smallCaps)
          el = <span style={{ fontVariant: "small-caps" }}>{el}</span>;
        if (run.superscript) el = <sup>{el}</sup>;
        if (run.subscript) el = <sub>{el}</sub>;

        // Inline styles (color, background, font)
        const inlineStyle: React.CSSProperties = {};
        if (run.color) inlineStyle.color = run.color;
        if (run.backgroundColor) inlineStyle.backgroundColor = run.backgroundColor;
        if (run.fontFamily) inlineStyle.fontFamily = run.fontFamily;
        if (run.fontSize) inlineStyle.fontSize = `${run.fontSize}pt`;

        const hasInlineStyle = Object.keys(inlineStyle).length > 0;

        if (run.link) {
          return (
            <a
              key={i}
              href={run.link}
              target="_blank"
              rel="noopener noreferrer"
              className="doc-link"
              style={hasInlineStyle ? inlineStyle : undefined}
            >
              {el}
            </a>
          );
        }

        if (hasInlineStyle) {
          return (
            <span key={i} style={inlineStyle}>
              {el}
            </span>
          );
        }

        return <Fragment key={i}>{el}</Fragment>;
      })}
    </>
  );
}

// ---------------------------------------------------------------------------
// Block style helper
// ---------------------------------------------------------------------------

function blockStyle(style?: BlockStyle): React.CSSProperties | undefined {
  if (!style) return undefined;
  const s: React.CSSProperties = {};
  if (style.alignment) s.textAlign = style.alignment;
  if (style.direction) s.direction = style.direction;
  if (style.backgroundColor) s.backgroundColor = style.backgroundColor;
  if (style.indentStart) s.paddingInlineStart = `${style.indentStart}pt`;
  return Object.keys(s).length > 0 ? s : undefined;
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

  const cls =
    (variant && classes[variant]) ||
    headingClasses[level] ||
    "";

  return (
    <Tag className={cls || undefined} style={blockStyle(style)}>
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
      <figure className="my-8" style={blockStyle(style)}>
        <RunRenderer runs={runs} />
      </figure>
    );
  }

  return (
    <p style={blockStyle(style)}>
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
  rows.forEach((row, ri) => {
    let ci = 0;
    row.forEach((cell) => {
      while (skipMap.has(`${ri}:${ci}`)) ci++;
      const rs = cell.rowSpan || 1;
      const cs = cell.colSpan || 1;
      for (let dr = 0; dr < rs; dr++) {
        for (let dc = 0; dc < cs; dc++) {
          if (dr !== 0 || dc !== 0) {
            skipMap.set(`${ri + dr}:${ci + dc}`, true);
          }
        }
      }
      ci += cs;
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
            const pStyle: React.CSSProperties = {};
            if (para.alignment) pStyle.textAlign = para.alignment;
            return (
              <div
                key={pi}
                style={Object.keys(pStyle).length > 0 ? pStyle : undefined}
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
            {bodyRows.map((row, ri) => {
              const actualRi = hasHeader ? ri + 1 : ri;
              const sectionRow = isSectionRow(row);

              // Section rows: single cell spanning all columns
              if (sectionRow) {
                const firstCell = row[0];
                return (
                  <tr key={ri} className="doc-table-row doc-table-section-row">
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
              for (let ci = 0; ci < row.length; ci++) {
                while (skipMap.has(`${actualRi}:${colIndex}`)) colIndex++;
                visibleCells.push({ cell: row[ci], key: ci });
                const cs = row[ci].colSpan || 1;
                colIndex += cs;
              }

              // Count remaining unfilled columns (not covered by rowSpan)
              let paddingCols = 0;
              for (let c = colIndex; c < numCols; c++) {
                if (!skipMap.has(`${actualRi}:${c}`)) paddingCols++;
              }

              return (
                <tr key={ri} className="doc-table-row">
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
  return (
    <figure className="my-8">
      <img
        src={url}
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
              <a href={item.link} className="doc-toc-link">
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
    case "table_of_contents":
      return <TocBlock items={(block as any).items || []} />;
    case "horizontal_rule":
      return <HorizontalRule />;
    default:
      return null;
  }
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function DocViewer({ blocks }: DocViewerProps) {
  return (
    <article className="doc-viewer">
      {blocks.map((block, i) => (
        <BlockRenderer key={i} block={block} />
      ))}
    </article>
  );
}

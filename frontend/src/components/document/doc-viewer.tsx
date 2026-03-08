"use client";

import { Fragment } from "react";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { FileText, User, Link as LinkIcon } from "lucide-react";

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
  // Special types
  type?: "image" | "person" | "rich_link" | "footnote_ref" | "equation" | "auto_text" | "drawing" | "chart" | "chip_placeholder" | "date";
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
  columnWidths?: (number | null)[];
  style?: BlockStyle;
  variant?: "title" | "subtitle";
  // Image block (positioned)
  url?: string;
  alt?: string;
  width?: number;
  height?: number;
  // TOC
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
        // Special types
        if (run.type === "image" && run.url) {
          return (
            <img
              key={i}
              src={run.url}
              alt={run.alt || ""}
              className="my-4 max-w-full rounded-md inline-block"
              style={run.width ? { maxWidth: `${run.width}pt` } : undefined}
            />
          );
        }

        if (run.type === "person") {
          return (
            <span
              key={i}
              className="inline-flex items-center gap-1 rounded-full bg-secondary/30 px-2 py-0.5 text-sm"
              title={run.email}
            >
              <User className="size-3" />
              {run.text}
            </span>
          );
        }

        if (run.type === "rich_link") {
          return (
            <a
              key={i}
              href={run.link}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 rounded-md bg-secondary/20 px-2 py-0.5 text-sm text-primary hover:bg-secondary/30 transition-colors"
            >
              <LinkIcon className="size-3" />
              {run.text}
            </a>
          );
        }

        if (run.type === "footnote_ref") {
          return (
            <sup key={i}>
              <a
                href={`#fn-${run.footnoteId}`}
                className="text-primary text-xs hover:underline"
              >
                [{run.number}]
              </a>
            </sup>
          );
        }

        if (run.type === "chip_placeholder") {
          return (
            <span
              key={i}
              className="inline-flex items-center rounded-full border border-border bg-muted px-2 py-0.5 text-xs text-muted-foreground"
            >
              Smart chip
            </span>
          );
        }

        if (run.type === "date") {
          return (
            <span
              key={i}
              className="inline-flex items-center gap-1 rounded-md border border-border bg-muted px-2 py-0.5 text-sm"
            >
              {run.text}
            </span>
          );
        }

        if (run.type === "equation") {
          return (
            <code key={i} className="rounded bg-muted px-1.5 py-0.5 text-sm">
              {run.text}
            </code>
          );
        }

        if (run.type === "auto_text") {
          return (
            <span key={i} className="text-muted-foreground text-sm">
              {run.text}
            </span>
          );
        }

        if (run.type === "drawing") {
          return (
            <span key={i} className="inline-flex items-center gap-1 rounded bg-muted px-2 py-1 text-sm text-muted-foreground">
              <FileText className="size-3" />
              {run.text}
            </span>
          );
        }

        if (run.type === "chart") {
          return (
            <span key={i} className="inline-flex items-center gap-1 rounded bg-muted px-2 py-1 text-sm text-muted-foreground">
              {run.text}
            </span>
          );
        }

        // Regular text run
        let el: React.ReactNode = run.text || "";

        if (run.bold) el = <strong>{el}</strong>;
        if (run.italic) el = <em>{el}</em>;
        if (run.underline) el = <u>{el}</u>;
        if (run.strikethrough) el = <s>{el}</s>;
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
              className="text-primary underline decoration-primary/30 underline-offset-2 hover:decoration-primary/60 transition-colors"
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
// Block renderers
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

  const extraClass =
    variant === "title"
      ? "text-4xl"
      : variant === "subtitle"
        ? "text-xl text-muted-foreground"
        : "";

  return (
    <Tag className={extraClass || undefined} style={blockStyle(style)}>
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
  if (!runs || runs.length === 0) return <br />;

  const hasOnlyImages = runs.every((r) => r.type === "image");
  if (hasOnlyImages) {
    return (
      <div className="my-4" style={blockStyle(style)}>
        <RunRenderer runs={runs} />
      </div>
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
    <Tag>
      {items.map((item, i) => (
        <li
          key={i}
          style={
            item.nesting > 0
              ? { marginLeft: `${item.nesting * 1.5}rem` }
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
}: {
  rows: TableCellData[][];
  hasHeader?: boolean;
  hasChips?: boolean;
}) {
  if (!rows || rows.length === 0) return null;

  // Track which cells to skip due to rowSpan/colSpan
  const skipMap = new Map<string, boolean>();
  rows.forEach((row, ri) => {
    let ci = 0;
    row.forEach((cell) => {
      // Skip covered cells
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

  function renderCell(
    cell: TableCellData,
    CellComponent: typeof TableCell | typeof TableHead
  ) {
    const cellStyle: React.CSSProperties = {};
    if (cell.backgroundColor) cellStyle.backgroundColor = cell.backgroundColor;
    if (cell.verticalAlign) cellStyle.verticalAlign = cell.verticalAlign;

    return (
      <CellComponent
        rowSpan={cell.rowSpan && cell.rowSpan > 1 ? cell.rowSpan : undefined}
        colSpan={cell.colSpan && cell.colSpan > 1 ? cell.colSpan : undefined}
        style={Object.keys(cellStyle).length > 0 ? cellStyle : undefined}
        className="whitespace-normal"
      >
        {cell.content.map((para, pi) => {
          const pStyle: React.CSSProperties = {};
          if (para.alignment) pStyle.textAlign = para.alignment;
          return (
            <div
              key={pi}
              style={Object.keys(pStyle).length > 0 ? pStyle : undefined}
              className={pi > 0 ? "mt-1" : undefined}
            >
              <RunRenderer runs={para.runs} />
            </div>
          );
        })}
      </CellComponent>
    );
  }

  return (
    <div className="my-4 not-prose">
      <Table>
        {headerRow && (
          <TableHeader>
            <TableRow>
              {headerRow.map((cell, ci) => (
                <Fragment key={ci}>{renderCell(cell, TableHead)}</Fragment>
              ))}
            </TableRow>
          </TableHeader>
        )}
        <TableBody>
          {bodyRows.map((row, ri) => {
            // Filter out cells that are covered by a rowSpan/colSpan
            const actualRi = hasHeader ? ri + 1 : ri;
            let colIndex = 0;
            const visibleCells: { cell: TableCellData; key: number }[] = [];
            for (let ci = 0; ci < row.length; ci++) {
              while (skipMap.has(`${actualRi}:${colIndex}`)) colIndex++;
              visibleCells.push({ cell: row[ci], key: ci });
              const cs = row[ci].colSpan || 1;
              colIndex += cs;
            }

            return (
              <TableRow key={ri}>
                {visibleCells.map(({ cell, key }) => (
                  <Fragment key={key}>{renderCell(cell, TableCell)}</Fragment>
                ))}
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
      {hasChips && (
        <p className="mt-2 text-xs text-muted-foreground">
          Some values in this table use Google Docs smart chips.{" "}
          <button
            type="button"
            className="underline hover:text-foreground transition-colors"
            onClick={() => {
              const editBtn = document.querySelector<HTMLButtonElement>("[data-edit-toggle]");
              editBtn?.click();
            }}
          >
            Switch to edit mode
          </button>
          {" "}to see all data.
        </p>
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
    <div className="my-4">
      <img
        src={url}
        alt={alt || ""}
        className="max-w-full rounded-md"
        style={width ? { maxWidth: `${width}pt` } : undefined}
      />
    </div>
  );
}

function TocBlock({ items }: { items: TocItem[] }) {
  return (
    <nav className="my-6 rounded-lg border bg-card p-4">
      <p className="mb-2 text-sm font-medium text-muted-foreground">
        Table of Contents
      </p>
      <ul className="space-y-1">
        {items.map((item, i) => (
          <li
            key={i}
            style={{ paddingLeft: `${item.level}rem` }}
          >
            {item.link ? (
              <a
                href={item.link}
                className="text-sm text-primary hover:underline"
              >
                {item.text}
              </a>
            ) : (
              <span className="text-sm">{item.text}</span>
            )}
          </li>
        ))}
      </ul>
    </nav>
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
      return <hr />;
    default:
      return null;
  }
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function DocViewer({ blocks }: DocViewerProps) {
  return (
    <article className="prose prose-neutral dark:prose-invert max-w-none">
      {blocks.map((block, i) => (
        <BlockRenderer key={i} block={block} />
      ))}
    </article>
  );
}

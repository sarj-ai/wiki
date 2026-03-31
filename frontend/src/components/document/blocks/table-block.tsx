import { Fragment } from "react";
import { Pencil } from "lucide-react";
import type { TableCellData } from "@/types";
import { RunRenderer } from "./run-renderer";
import { getStatusClass, VALID_VERTICAL_ALIGNS } from "./utils";

const VALID_TEXT_ALIGNS = new Set(["center", "right", "justify"]);

export function DocTableBlock({
  rows,
  hasHeader,
  hasChips,
  caption,
  columnWidths,
}: {
  rows: TableCellData[][];
  hasHeader?: boolean;
  hasChips?: boolean;
  caption?: string;
  columnWidths?: (number | null)[];
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
    if (cell.verticalAlign && VALID_VERTICAL_ALIGNS.has(cell.verticalAlign)) cellStyle.verticalAlign = cell.verticalAlign as React.CSSProperties["verticalAlign"];

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
            // Empty paragraph = newline spacer within the cell
            if (!para.runs || para.runs.length === 0) {
              return <div key={pi} className="h-3" aria-hidden />;
            }
            const paragraphStyle: React.CSSProperties = {};
            if (para.alignment && VALID_TEXT_ALIGNS.has(para.alignment)) paragraphStyle.textAlign = para.alignment;
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
        <table
          className="doc-table"
          style={columnWidths?.some((w) => w != null) ? { tableLayout: "fixed" } : undefined}
        >
          {columnWidths && columnWidths.some((w) => w != null) && (
            <colgroup>
              {(() => {
                // Convert pt widths to percentages for responsive rendering
                const total = columnWidths.reduce<number>((sum, w) => sum + (w ?? 0), 0);
                return columnWidths.map((w, i) => (
                  <col
                    key={i}
                    style={w != null && total > 0 ? { width: `${((w / total) * 100).toFixed(1)}%` } : undefined}
                  />
                ));
              })()}
            </colgroup>
          )}
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

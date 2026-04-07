"""Table transformation."""

from __future__ import annotations

from app.docs_transformer.constants import _ALIGNMENT_MAP
from app.docs_transformer.helpers import _dimension_pt
from app.docs_transformer.runs import _extract_runs


def _transform_table(
    table: dict, inline_objects: dict, lists_map: dict
) -> dict:
    rows: list[list[dict]] = []
    has_header = False
    has_chips = False

    # Track cells covered by rowSpan from previous rows: set of (row, col)
    row_span_covered: set[tuple[int, int]] = set()

    for ri, row in enumerate(table.get("tableRows", [])):
        row_style = row.get("tableRowStyle", {})
        if ri == 0 and row_style.get("tableHeader"):
            has_header = True

        cells: list[dict] = []
        col_idx = 0
        skip_count = 0
        for cell in row.get("tableCells", []):
            # Skip cells covered by a previous cell's colSpan
            if skip_count > 0:
                skip_count -= 1
                continue

            # Skip cells covered by rowSpan from previous rows
            if (ri, col_idx) in row_span_covered:
                col_idx += 1
                continue

            cell_style = cell.get("tableCellStyle", {})
            row_span = cell_style.get("rowSpan", 1)
            col_span = cell_style.get("columnSpan", 1)

            # Mark cells covered by this cell's rowSpan for future rows
            for r_offset in range(1, row_span):
                for c_offset in range(col_span):
                    row_span_covered.add((ri + r_offset, col_idx + c_offset))

            # Extract cell content (can be multiple paragraphs)
            cell_content: list[dict] = []
            for content_el in cell.get("content", []):
                if "paragraph" in content_el:
                    para_runs = _extract_runs(
                        content_el["paragraph"].get("elements", []),
                        inline_objects,
                    )
                    para_style = content_el["paragraph"].get("paragraphStyle", {})
                    alignment = para_style.get("alignment")
                    if para_runs:
                        for run in para_runs:
                            if run.get("type") == "chip_placeholder":
                                has_chips = True
                        para_block: dict = {"runs": para_runs}
                        if alignment and alignment != "START" and alignment in _ALIGNMENT_MAP:
                            para_block["alignment"] = _ALIGNMENT_MAP[alignment]
                        cell_content.append(para_block)
                    else:
                        # Empty paragraph = newline spacer within the cell
                        cell_content.append({"runs": []})

            cell_data: dict = {
                "content": cell_content if cell_content else [{"runs": []}],
            }

            if row_span > 1:
                cell_data["rowSpan"] = row_span
            if col_span > 1:
                cell_data["colSpan"] = col_span

            # Extract cell background color
            bg = cell_style.get("backgroundColor", {})
            if bg:
                r = bg.get("color", {}).get("rgbColor", {})
                red = r.get("red", 0)
                green = r.get("green", 0)
                blue = r.get("blue", 0)
                # Skip white/near-white and light gray backgrounds
                # (default bg + Google's header gray are handled by CSS)
                if not (red > 0.9 and green > 0.9 and blue > 0.9):
                    ri_val = round(red * 255)
                    gi_val = round(green * 255)
                    bi_val = round(blue * 255)
                    cell_data["backgroundColor"] = f"#{ri_val:02x}{gi_val:02x}{bi_val:02x}"

            v_align = cell_style.get("contentAlignment")
            if v_align and v_align != "TOP":
                cell_data["verticalAlign"] = v_align.lower()

            cells.append(cell_data)
            col_idx += col_span

            # Skip the next (colSpan - 1) cells in the API response
            if col_span > 1:
                skip_count = col_span - 1

        rows.append(cells)

    # NOTE: Caption extraction disabled — it was pulling styled section
    # header rows (like "DECISIONS MADE AT ANY PHASE") out of the table,
    # breaking the layout. These rows are now handled by the frontend's
    # isSectionRow detection which keeps them inside the table.
    caption = None

    result: dict = {"type": "table", "rows": rows}
    if has_header:
        result["hasHeader"] = True
    if caption:
        result["caption"] = caption
    if has_chips:
        result["hasChips"] = True

    # Column widths live in tableStyle.tableColumnProperties (not top-level)
    col_props = (
        table.get("tableStyle", {}).get("tableColumnProperties", [])
        or table.get("tableColumnProperties", [])
    )
    if col_props:
        widths = [_dimension_pt(cp.get("width")) for cp in col_props]
        if any(w is not None for w in widths):
            result["columnWidths"] = widths

    return result

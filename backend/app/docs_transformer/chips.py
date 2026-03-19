"""Smart chip enrichment from HTML export."""

from __future__ import annotations


def enrich_chips_from_html(blocks: list[dict], html: str) -> list[dict]:
    """Replace chip_placeholder runs with real values extracted from the HTML export.

    Google's HTML export renders smart chips (Person, Status, File, Date, etc.)
    as actual text, while the Docs API v1 returns only opaque Unicode placeholders.

    This function parses the HTML tables and merges the real values back in.
    """
    from html.parser import HTMLParser

    # -----------------------------------------------------------------------
    # Step 1: Parse HTML tables -> list of tables, each a list of rows,
    #         each a list of cell texts.
    # -----------------------------------------------------------------------

    class _TableParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.tables: list[list[list[str]]] = []
            self._in_table = False
            self._in_cell = False
            self._current_table: list[list[str]] = []
            self._current_row: list[str] = []
            self._current_cell: list[str] = []

        def handle_starttag(self, tag, attrs):
            if tag == "table":
                self._in_table = True
                self._current_table = []
            elif tag == "tr" and self._in_table:
                self._current_row = []
            elif tag in ("td", "th") and self._in_table:
                self._in_cell = True
                self._current_cell = []

        def handle_endtag(self, tag):
            if tag in ("td", "th") and self._in_cell:
                self._in_cell = False
                text = "".join(self._current_cell).strip()
                self._current_row.append(text)
            elif tag == "tr" and self._in_table:
                if self._current_row:
                    self._current_table.append(self._current_row)
            elif tag == "table" and self._in_table:
                self._in_table = False
                if self._current_table:
                    self.tables.append(self._current_table)

        def handle_data(self, data):
            if self._in_cell:
                self._current_cell.append(data)

    parser = _TableParser()
    parser.feed(html)
    html_tables = parser.tables

    # -----------------------------------------------------------------------
    # Step 2: Walk through blocks, match each "table" block to an HTML table
    #         by order, and replace chip_placeholder runs with real text.
    # -----------------------------------------------------------------------

    html_table_idx = 0
    for block in blocks:
        if block.get("type") != "table" or not block.get("hasChips"):
            if block.get("type") == "table":
                html_table_idx += 1
            continue

        if html_table_idx >= len(html_tables):
            html_table_idx += 1
            continue

        html_rows = html_tables[html_table_idx]
        block_rows = block.get("rows", [])

        # If a caption row was extracted from the block, the HTML still has it
        # as the first row.  Skip it so row indices align.
        if block.get("caption") and html_rows:
            html_rows = html_rows[1:]

        for ri, row_cells in enumerate(block_rows):
            if ri >= len(html_rows):
                break
            html_row = html_rows[ri]

            for ci, cell in enumerate(row_cells):
                if ci >= len(html_row):
                    break
                html_text = html_row[ci]
                if not html_text:
                    continue

                # Check if this cell has any chip_placeholder runs
                for para in cell.get("content", []):
                    runs = para.get("runs", [])
                    has_chip = any(r.get("type") == "chip_placeholder" for r in runs)
                    if has_chip and len(runs) == 1 and runs[0].get("type") == "chip_placeholder":
                        # Entire cell is a single chip — replace with HTML text
                        runs[0] = {"text": html_text}

        html_table_idx += 1

    return blocks

"""Monospace paragraph detection and code block grouping."""

from __future__ import annotations


def _is_mono_paragraph(block: dict) -> bool:
    """Check if a paragraph block is entirely monospace (code)."""
    if block.get("type") != "paragraph":
        return False
    runs = block.get("runs", [])
    if not runs:
        return False
    # All text-bearing runs must be monospace (and there must be at least one)
    text_runs = [r for r in runs if r.get("text", "").strip()]
    if not text_runs:
        return False
    return all(r.get("_mono") for r in text_runs)


def _group_code_blocks(blocks: list[dict]) -> list[dict]:
    """Group consecutive monospace paragraphs into code_block blocks.

    Empty paragraphs between monospace paragraphs are treated as blank
    lines within the same code block (not as a break).

    Also converts isolated monospace runs within normal paragraphs into
    inline code (type: "code").
    """
    result: list[dict] = []
    code_lines: list[str] = []
    # Buffer for empty paragraphs that might sit between code lines
    pending_empties: int = 0

    def flush_code():
        nonlocal pending_empties
        if code_lines:
            # Strip trailing blank lines
            while code_lines and code_lines[-1] == "":
                code_lines.pop()
            result.append({"type": "code_block", "code": "\n".join(code_lines)})
            code_lines.clear()
        pending_empties = 0

    def is_empty_paragraph(block: dict) -> bool:
        if block.get("type") != "paragraph":
            return False
        runs = block.get("runs")
        if not runs:
            return True
        # A paragraph with image/drawing/chart runs is NOT empty
        return all(
            not r.get("text", "").strip() and r.get("type") not in ("image", "drawing", "chart")
            for r in runs
        )

    for block in blocks:
        if _is_mono_paragraph(block):
            # Commit any pending empty lines as blank lines in the code
            for _ in range(pending_empties):
                code_lines.append("")
            pending_empties = 0
            line = "".join(r.get("text", "") for r in block.get("runs", []))
            code_lines.append(line)
        elif is_empty_paragraph(block) and code_lines:
            # Potentially a blank line inside a code block — buffer it
            pending_empties += 1
        else:
            flush_code()
            # Re-emit any buffered empties as normal spacer paragraphs
            for _ in range(pending_empties):
                result.append({"type": "paragraph", "runs": []})
            pending_empties = 0
            # Check for inline code: mixed paragraphs with some mono runs
            if block.get("type") == "paragraph" and block.get("runs"):
                for run in block["runs"]:
                    if run.get("_mono") and run.get("text", "").strip():
                        run["type"] = "code"
                    run.pop("_mono", None)
            result.append(block)

    flush_code()
    return result

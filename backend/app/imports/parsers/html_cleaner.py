"""Clean Notion HTML for better Google Docs conversion.

Google Docs' HTML import handles: h1-h6, p, ul, ol, li, table, strong, em,
a, img, blockquote, pre, br, hr. It ignores all CSS.
"""

from __future__ import annotations

import re


def clean_notion_html(html: str) -> str:
    """Clean Notion HTML for Google Docs import."""

    # Remove <style> and <script> blocks
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL)
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)

    # Remove page-title h1 (duplicates the doc title)
    html = re.sub(
        r'<h1[^>]*page-title[^>]*>.*?</h1>', "", html, flags=re.DOTALL,
    )

    # Convert toggles to bold title + indented content
    # <details><summary>Title</summary>...content...</details>
    html = re.sub(
        r"<details[^>]*>\s*<summary[^>]*>(.*?)</summary>(.*?)</details>",
        r"<p><strong>▸ \1</strong></p>\2",
        html,
        flags=re.DOTALL,
    )

    # Convert callout figures to blockquotes
    html = re.sub(
        r'<figure[^>]*callout[^>]*>(.*?)</figure>',
        r"<blockquote>\1</blockquote>",
        html,
        flags=re.DOTALL,
    )

    # Convert Notion to-do lists: <div class="checkbox checkbox-off/on"> + <span>
    # Checked:
    html = re.sub(
        r'<div[^>]*checkbox-on[^>]*></div>\s*<span[^>]*>(.*?)</span>',
        r"☑ \1",
        html,
        flags=re.DOTALL,
    )
    # Unchecked:
    html = re.sub(
        r'<div[^>]*checkbox-off[^>]*></div>\s*<span[^>]*>(.*?)</span>',
        r"☐ \1",
        html,
        flags=re.DOTALL,
    )
    # Also handle <input type="checkbox"> style (older Notion exports)
    html = re.sub(
        r'<input[^>]*checked[^>]*/?>',
        "☑ ",
        html,
    )
    html = re.sub(
        r'<input[^>]*type="checkbox"[^>]*/?>',
        "☐ ",
        html,
    )

    # Flatten column layouts
    html = re.sub(r'<div[^>]*column-list[^>]*>', "<div>", html)
    html = re.sub(r'<div[^>]*column[^"]*"[^>]*>', "<div>", html)

    # Convert Notion TOC nav to a simple div
    html = re.sub(r'<nav[^>]*>(.*?)</nav>', r"<div>\1</div>", html, flags=re.DOTALL)

    # Strip class, style, id, dir attributes
    html = re.sub(r'\s+class="[^"]*"', "", html)
    html = re.sub(r'\s+style="[^"]*"', "", html)
    html = re.sub(r'\s+id="[^"]*"', "", html)
    html = re.sub(r'\s+dir="[^"]*"', "", html)

    # Clean up empty elements
    html = re.sub(r"<div>\s*</div>", "", html)
    html = re.sub(r"<p>\s*</p>", "", html)
    html = re.sub(r"\n{3,}", "\n\n", html)

    return html

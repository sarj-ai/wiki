"""Heading ID assignment and TOC link resolution."""

from __future__ import annotations

import re


def _slugify(text: str) -> str:
    """Create a URL-friendly slug from heading text."""
    slug = text.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    return slug.strip("-") or "heading"


def _assign_heading_ids_and_resolve_toc(blocks: list[dict]) -> list[dict]:
    """Assign stable IDs to headings and resolve TOC links by matching text.

    Google Docs doesn't always provide headingId in paragraphStyle. This
    function generates slug-based IDs for headings that lack one, then
    resolves TOC item links by matching heading text -> generated ID.
    """
    # Pass 1: assign IDs to headings
    slug_counts: dict[str, int] = {}
    heading_text_to_id: dict[str, str] = {}

    for block in blocks:
        if block.get("type") != "heading":
            continue
        # If headingId already set by Google, keep it
        if block.get("headingId"):
            text = "".join(r.get("text", "") for r in block.get("runs", [])).strip()
            if text:
                heading_text_to_id[text] = block["headingId"]
            continue

        text = "".join(r.get("text", "") for r in block.get("runs", [])).strip()
        if not text:
            continue

        slug = _slugify(text)
        count = slug_counts.get(slug, 0)
        slug_counts[slug] = count + 1
        heading_id = slug if count == 0 else f"{slug}-{count}"

        block["headingId"] = heading_id
        heading_text_to_id[text] = heading_id

    # Pass 2: resolve TOC links
    for block in blocks:
        if block.get("type") != "table_of_contents":
            continue
        for item in block.get("tocItems", []):
            if item.get("link"):
                continue  # already has a link
            text = item.get("text", "").strip()
            hid = heading_text_to_id.get(text)
            if not hid:
                # Numbered TOC entries may include a prefix like "1. Overview"
                # or "1.2.3 Overview" -- strip it and retry the match.
                stripped = re.sub(r"^[\d]+(?:\.[\d]+)*\.?\s+", "", text)
                if stripped and stripped != text:
                    hid = heading_text_to_id.get(stripped)
            if hid:
                item["link"] = f"#heading={hid}"

    return blocks

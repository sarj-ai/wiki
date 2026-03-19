"""Table of Contents transformation."""

from __future__ import annotations

from app.docs_transformer.helpers import _dimension_pt
from app.docs_transformer.runs import _extract_runs


def _transform_toc(toc: dict) -> dict:
    items: list[dict] = []
    for content_el in toc.get("content", []):
        if "paragraph" not in content_el:
            continue
        para = content_el["paragraph"]
        style = para.get("paragraphStyle", {})
        indent = _dimension_pt(style.get("indentStart"))
        level = 0
        if indent:
            level = max(0, int(indent / 36))  # ~36pt per indent level

        runs = _extract_runs(para.get("elements", []), {})
        text = "".join(r.get("text", "") for r in runs)

        # Extract link: TOC entries use headingId (not url) for internal links
        link = None
        for el in para.get("elements", []):
            text_style = el.get("textRun", {}).get("textStyle", {})
            link_obj = text_style.get("link")
            if link_obj:
                heading_id = link_obj.get("headingId")
                if heading_id:
                    link = f"#heading={heading_id}"
                elif link_obj.get("url"):
                    link = link_obj["url"]
                break

        if text.strip():
            items.append({"text": text.strip(), "level": level, "link": link})

    return {"type": "table_of_contents", "tocItems": items}

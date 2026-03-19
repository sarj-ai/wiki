"""Run extraction from paragraph elements."""

from __future__ import annotations

import re
from datetime import datetime

from app.docs_transformer.constants import _MONOSPACE_FONTS
from app.docs_transformer.objects import _extract_inline_object


def _extract_runs(
    elements: list[dict],
    inline_objects: dict,
    footnotes: dict | None = None,
    footnote_index: dict[str, int] | None = None,
) -> list[dict]:
    """Extract text and inline element runs from paragraph elements."""
    runs: list[dict] = []

    for el in elements:
        if "textRun" in el:
            run = _extract_text_run(el["textRun"])
            if run:
                runs.append(run)

        elif "inlineObjectElement" in el:
            run = _extract_inline_object(el["inlineObjectElement"], inline_objects)
            if run:
                runs.append(run)

        elif "person" in el:
            person = el["person"]
            props = person.get("personProperties", {})
            name = props.get("name", "")
            email = props.get("email", "")
            runs.append({
                "type": "person",
                "text": name or email,
                "email": email,
            })

        elif "richLink" in el:
            rich_link = el["richLink"]
            props = rich_link.get("richLinkProperties", {})
            title = props.get("title", "")
            uri = props.get("uri", "")
            runs.append({
                "type": "rich_link",
                "text": title or uri,
                "link": uri,
                "mimeType": props.get("mimeType"),
            })

        elif "footnoteReference" in el:
            fn_id = el["footnoteReference"].get("footnoteId", "")
            fn_num = el["footnoteReference"].get("footnoteNumber", "")
            if footnote_index is not None and fn_id:
                if fn_id not in footnote_index:
                    footnote_index[fn_id] = int(fn_num) if fn_num else len(footnote_index) + 1
            runs.append({
                "type": "footnote_ref",
                "footnoteId": fn_id,
                "number": fn_num or str(len(footnote_index or {})),
            })

        elif "equation" in el:
            runs.append({"type": "equation", "text": "[equation]"})

        elif "dateElement" in el:
            date_el = el["dateElement"]
            props = date_el.get("dateElementProperties", {})
            timestamp = props.get("timestamp", "")
            if timestamp:
                try:
                    dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                    text = dt.strftime("%b %d, %Y %I:%M %p")
                except (ValueError, AttributeError):
                    text = timestamp
            else:
                text = "Date"
            runs.append({"type": "date", "text": text})

        elif "autoText" in el:
            auto_type = el["autoText"].get("type", "")
            runs.append({"type": "auto_text", "text": auto_type.replace("_", " ").title()})

        # pageBreak, columnBreak — skip silently

    return runs


def _extract_text_run(text_run: dict) -> dict | None:
    content = text_run.get("content", "")
    style = text_run.get("textStyle", {})

    # Strip trailing newline
    if content.endswith("\n"):
        content = content[:-1]

    # Convert vertical tabs (Shift+Enter in Google Docs) to newlines
    # so the frontend can render them as <br> line breaks
    content = content.replace("\x0b", "\n")

    # Strip private-use Unicode chars (Google Docs chip placeholders like \ue907).
    # These represent "building block" smart chips (Person, Status, File, Date, etc.)
    # whose actual values are NOT exposed by the Docs API v1.
    if re.search(r"[\ue000-\uf8ff]", content):
        stripped = re.sub(r"[\ue000-\uf8ff]", "", content).strip()
        if not stripped:
            # Cell contains only chip placeholders — mark as chip so frontend
            # can render a subtle placeholder instead of noisy "Smart chip" pills.
            return {"type": "chip_placeholder"}
        # Otherwise just strip the private-use chars from the text
        content = re.sub(r"[\ue000-\uf8ff]", "", content)

    if not content:
        return None

    run: dict = {"text": content}

    if style.get("bold"):
        run["bold"] = True
    if style.get("italic"):
        run["italic"] = True
    if style.get("underline"):
        # Don't mark underline if it's just a link (Google Docs auto-underlines links)
        if not style.get("link"):
            run["underline"] = True
    if style.get("strikethrough"):
        run["strikethrough"] = True
    if style.get("smallCaps"):
        run["smallCaps"] = True
    if style.get("baselineOffset") == "SUPERSCRIPT":
        run["superscript"] = True
    if style.get("baselineOffset") == "SUBSCRIPT":
        run["subscript"] = True

    link = style.get("link")
    if link:
        url = link.get("url")
        if url:
            run["link"] = url

    # Mark monospace font for code detection
    font = (
        style.get("weightedFontFamily", {}).get("fontFamily", "")
        or style.get("fontFamily", "")
    )
    if font and font.lower() in _MONOSPACE_FONTS:
        run["_mono"] = True

    return run

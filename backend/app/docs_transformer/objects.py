"""Extract inline and positioned objects (images, drawings, charts)."""

from __future__ import annotations


def _extract_inline_object(
    el: dict, inline_objects: dict
) -> dict | None:
    obj_id = el.get("inlineObjectId")
    if not obj_id or obj_id not in inline_objects:
        return None

    obj = inline_objects[obj_id]
    embedded = (
        obj.get("inlineObjectProperties", {}).get("embeddedObject", {})
    )

    # Image
    image_props = embedded.get("imageProperties", {})
    content_uri = image_props.get("contentUri")
    if content_uri:
        size = embedded.get("size", {})
        width_pt = size.get("width", {}).get("magnitude", 0)
        height_pt = size.get("height", {}).get("magnitude", 0)

        # Skip tiny images (< 24pt) — these are Google Docs internal column-type
        # icons (person, status, text, date icons) that aren't publicly accessible.
        if width_pt > 0 and height_pt > 0 and width_pt < 24 and height_pt < 24:
            return None

        run: dict = {"type": "image", "url": content_uri}
        title = embedded.get("title")
        desc = embedded.get("description")
        if desc:
            run["alt"] = desc
        elif title:
            run["alt"] = title

        if width_pt:
            run["width"] = width_pt
        if height_pt:
            run["height"] = height_pt
        return run

    # Embedded drawing (opaque — just note it exists)
    if embedded.get("embeddedDrawingProperties") is not None:
        return {"type": "drawing", "text": "[Drawing]"}

    # Linked content (Sheets chart)
    linked = embedded.get("linkedContentReference", {})
    chart_ref = linked.get("sheetsChartReference")
    if chart_ref:
        return {
            "type": "chart",
            "text": "[Embedded Chart]",
            "spreadsheetId": chart_ref.get("spreadsheetId"),
            "chartId": chart_ref.get("chartId"),
        }

    return None


def _extract_positioned_object(
    obj_id: str, positioned_objects: dict
) -> dict | None:
    obj = positioned_objects.get(obj_id)
    if not obj:
        return None

    embedded = (
        obj.get("positionedObjectProperties", {}).get("embeddedObject", {})
    )
    image_props = embedded.get("imageProperties", {})
    content_uri = image_props.get("contentUri")
    if content_uri:
        block: dict = {
            "type": "image",
            "url": content_uri,
        }
        title = embedded.get("title")
        desc = embedded.get("description")
        if desc:
            block["alt"] = desc
        elif title:
            block["alt"] = title

        size = embedded.get("size", {})
        width_pt = size.get("width", {}).get("magnitude")
        height_pt = size.get("height", {}).get("magnitude")
        if width_pt:
            block["width"] = width_pt
        if height_pt:
            block["height"] = height_pt
        return block

    return None

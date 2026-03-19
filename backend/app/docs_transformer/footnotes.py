"""Footnote section builder."""

from __future__ import annotations

from app.docs_transformer.runs import _extract_runs


def _build_footnote_section(
    footnotes: dict,
    footnote_index: dict[str, int],
    inline_objects: dict,
    lists_map: dict,
) -> list[dict]:
    blocks: list[dict] = []
    blocks.append({"type": "heading", "level": 4, "runs": [{"text": "Footnotes"}]})

    for fn_id, fn_num in sorted(footnote_index.items(), key=lambda x: x[1]):
        fn = footnotes.get(fn_id)
        if not fn:
            continue
        fn_content = fn.get("content", [])
        fn_runs: list[dict] = [{"text": f"{fn_num}. ", "bold": True}]
        for content_el in fn_content:
            if "paragraph" in content_el:
                para_runs = _extract_runs(
                    content_el["paragraph"].get("elements", []),
                    inline_objects,
                )
                fn_runs.extend(para_runs)

        blocks.append({"type": "paragraph", "runs": fn_runs})

    return blocks

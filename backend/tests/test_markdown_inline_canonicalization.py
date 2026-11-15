from copy import deepcopy
import json
from pathlib import Path
from typing import Literal

import pytest

from app.document_transform.docx_renderer import markdown_import_manifest
from app.document_transform.markdown_import import verify_markdown_import
from app.document_transform.verification import BrandVerification


MARKDOWN = (
    "# Native preservation canary\n\n"
    "**Bold label:** plain text with *italic emphasis* and "
    "[an exact link](https://www.davidzwirner.com/exhibitions/2018/david-zwirner-25-years).\n\n"
    "Plain text must not become bold."
)


def _document():
    return json.loads(
        (
            Path(__file__).parent / "data" / "markdown_inline_emphasis" / "google_native.json"
        ).read_text()
    )


def _manifest():
    return markdown_import_manifest(
        MARKDOWN,
        title="Private Inline Emphasis Preservation Canary",
        branded=True,
        header_title="Private Inline Emphasis Preservation Canary — Sarj Formatted",
        compact_single_paragraph=True,
    )


def test_captured_google_readback_accepts_only_adjacent_runs_with_identical_planned_style() -> None:
    result = verify_markdown_import(_document(), _manifest())
    assert result.content_preserved
    assert result.structure_preserved
    assert result.brand is BrandVerification.FULL


@pytest.mark.parametrize("corruption", ["text", "link", "italic", "extra-bold"])
def test_planned_run_coalescing_does_not_hide_meaningful_native_changes(
    corruption: Literal["text", "link", "italic", "extra-bold"],
) -> None:
    document = deepcopy(_document())
    paragraph = document["tabs"][0]["documentTab"]["body"]["content"][2]["paragraph"]
    runs = [element["textRun"] for element in paragraph["elements"] if "textRun" in element]
    match corruption:
        case "text":
            runs[0]["content"] = runs[0]["content"].replace("label", "false")
        case "link":
            next(run for run in runs if "link" in run["textStyle"])["textStyle"]["link"]["url"] = (
                "https://example.test/changed"
            )
        case "italic":
            next(run for run in runs if run["textStyle"].get("italic"))["textStyle"]["italic"] = (
                False
            )
        case "extra-bold":
            runs[0]["textStyle"]["bold"] = True
    result = verify_markdown_import(document, _manifest())
    assert not (
        result.content_preserved
        and result.structure_preserved
        and (result.brand in {BrandVerification.FULL, BrandVerification.NOT_REQUIRED})
    )

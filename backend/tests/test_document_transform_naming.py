from __future__ import annotations

import pytest

from app.aliases.schemas import DocumentAliasWrite
from app.document_transform.ir import BlockKind, DocumentIR, TextBlock
from app.document_transform.naming import polish_alias_candidates, suggested_document_title
from app.identifiers import FileId


def _document(title: str, heading: str, *, level: int = 1) -> DocumentIR:
    return DocumentIR(
        title=title,
        blocks=(TextBlock(id="title", kind=BlockKind.HEADING, text=heading, level=level),),
        source_format="google_docs",
    )


def test_suggested_title_uses_leading_heading_for_placeholder() -> None:
    assert suggested_document_title(
        _document("Untitled document", "Platform reliability plan")
    ) == ("Platform reliability plan")


@pytest.mark.parametrize("heading", ["Overview", "Introduction", "x" * 256, "\u202eDanger"])
def test_suggested_title_rejects_generic_or_unsafe_heading(heading: str) -> None:
    assert suggested_document_title(_document("Untitled document", heading)) == "Untitled document"


def test_suggested_title_preserves_intentional_filename() -> None:
    assert suggested_document_title(_document("2026 Board Pack", "Executive summary")) == (
        "2026 Board Pack"
    )


@pytest.mark.parametrize("title", ["  Board  Pack  ", "x" * 300, "خطة  العمل"])
def test_suggested_title_preserves_authored_filename_exactly(title: str) -> None:
    assert suggested_document_title(_document(title, "Executive summary")) == title


def test_suggested_title_does_not_use_subheading_or_body_text() -> None:
    assert suggested_document_title(_document("Untitled", "Risks", level=2)) == "Untitled"
    document = DocumentIR(
        title="Untitled",
        blocks=(TextBlock(id="body", kind=BlockKind.PARAGRAPH, text="Secret body sentence"),),
        source_format="google_docs",
    )
    assert suggested_document_title(document) == "Untitled"


@pytest.mark.parametrize(
    "title", ["", "خطة العمل", "2026 Roadmap", "Équipe d’ingénierie", "x" * 500]
)
def test_alias_candidates_are_valid_stable_bounded_and_file_specific(title: str) -> None:
    candidates = polish_alias_candidates(FileId("doc-one"), title)
    assert candidates == polish_alias_candidates(FileId("doc-one"), title)
    assert len(set(candidates)) == 4
    assert set(candidates).isdisjoint(polish_alias_candidates(FileId("doc-two"), title))
    for candidate in candidates:
        assert DocumentAliasWrite(alias=candidate).alias == candidate

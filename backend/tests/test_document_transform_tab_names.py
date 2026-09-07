from dataclasses import replace
from typing import override
import uuid

import pytest

from app.document_transform.ir import BlockKind, DocumentIR, SourceRange, TextBlock, utf16_length
from app.document_transform.schemas import DocumentTransformRequest, MarkdownSource, TransformMode
from app.document_transform.tab_names import (
    MAX_TAB_TITLE_UNITS,
    NativeTabProperties,
    plan_tab_names,
)
from app.identifiers import DocsRevisionId, DocsTabId, FileId, TaskId
from tests.test_document_transform import _copy_executor, _CopyCheckpointStore, _Gateway


def _document(*headings: str) -> DocumentIR:
    return DocumentIR(
        title="Document",
        source_format="google_doc",
        tab_ids=tuple(DocsTabId(f"t{index}") for index in range(len(headings))),
        blocks=tuple(
            TextBlock(
                id=f"b{index}",
                kind=BlockKind.HEADING,
                text=heading,
                level=1,
                source_range=SourceRange(1, 1 + utf16_length(heading), DocsTabId(f"t{index}")),
            )
            for index, heading in enumerate(headings)
        ),
    )


def _tabs(*titles: str) -> tuple[NativeTabProperties, ...]:
    return tuple(
        NativeTabProperties(DocsTabId(f"t{index}"), title, index=index)
        for index, title in enumerate(titles)
    )


def test_generic_tabs_receive_source_grounded_native_names():
    document = _document("Risks and mitigations", "Project roadmap")
    tabs = _tabs("Tab 1", "Untitled tab")
    plan = plan_tab_names(document, tabs)
    assert [rename.title for rename in plan.renames] == [
        "Risks and Mitigations",
        "Project Roadmap",
    ]
    assert plan.renames[0].request() == {
        "updateDocumentTabProperties": {
            "tabProperties": {"tabId": "t0", "title": "Risks and Mitigations", "iconEmoji": "⚠️"},
            "fields": "title,iconEmoji",
        },
    }
    assert plan.matches_preimage(tabs)
    result = tuple(
        replace(tab, title=rename.title, icon_emoji=rename.icon_emoji)
        for tab, rename in zip(tabs, plan.renames, strict=True)
    )
    assert plan.matches_result(result)
    assert not plan.matches_preimage(result)
    assert plan_tab_names(document, result).renames == ()


def test_authored_titles_are_preserved_and_collisions_are_bounded():
    document = _document("Risks", "Risks", "RISKS")
    plan = plan_tab_names(document, _tabs("Risks", "Tab 2", "Tab 3"))
    assert [rename.title for rename in plan.renames] == ["Risks", "Risks (2)", "RISKS (3)"]


def test_existing_tab_title_is_title_cased_and_trailing_date_is_removed_once():
    document = _document("Security review")
    tabs = _tabs("security review 2026-09-04")
    plan = plan_tab_names(document, tabs)
    assert [rename.title for rename in plan.renames] == ["Security Review"]
    result = (replace(tabs[0], title="Security Review", icon_emoji="🔒"),)
    assert plan.matches_result(result)
    assert plan_tab_names(document, result).renames == ()


def test_nested_siblings_use_separate_title_namespaces():
    document = _document("Risks", "Risks")
    tabs = _tabs("Tab 1", "Tab 2")
    tabs = (tabs[0], replace(tabs[1], parent_tab_id=tabs[0].tab_id, index=0))
    plan = plan_tab_names(document, tabs)
    assert [rename.title for rename in plan.renames] == ["Risks", "Risks"]


def test_non_heading_content_and_dates_do_not_invent_tab_purpose():
    document = _document("2026-09-04", "Ordinary first paragraph", "Low-level subsection")
    document = replace(
        document,
        blocks=(
            document.blocks[0],
            replace(document.blocks[1], kind=BlockKind.PARAGRAPH),
            replace(document.blocks[2], level=2),
        ),
    )
    assert [
        rename.title
        for rename in plan_tab_names(document, _tabs("Tab 1", "Tab 2", "Tab 3")).renames
    ] == ["Tab 1", "Tab 2", "Tab 3"]


def test_rtl_and_astral_titles_are_bounded_and_control_characters_removed():
    document = _document("خطة العمل", "🚀 Launch " * 15, "Safe\u202e title\n name")
    plan = plan_tab_names(document, _tabs("", "Tab 2", "Tab 3"))
    assert plan.renames[0].title == "خطة العمل"
    assert utf16_length(plan.renames[1].title) <= MAX_TAB_TITLE_UNITS
    assert plan.renames[2].title == "Safe Title Name"


def test_exact_result_verification_rejects_concurrent_rename_or_topology_change():
    document = _document("Roadmap", "Risk register")
    tabs = _tabs("Tab 1", "Existing author title")
    plan = plan_tab_names(document, tabs)
    result = (
        replace(tabs[0], title="Roadmap", icon_emoji="📄"),
        replace(tabs[1], title="Existing Author Title", icon_emoji="📄"),
    )
    assert plan.matches_result(result)
    assert not plan.matches_result((result[0], replace(result[1], title="User rename")))
    assert not plan.matches_result((replace(result[0], index=3), result[1]))
    assert not plan.matches_result(
        (replace(result[0], parent_tab_id=DocsTabId("other")), result[1])
    )
    assert not plan.matches_result(tuple(reversed(result)))


def test_incomplete_or_duplicate_tab_metadata_produces_no_writes():
    document = _document("Roadmap", "Risks")
    tabs = _tabs("Tab 1", "Tab 2")
    assert plan_tab_names(document, tabs[:1]).renames == ()
    duplicate_document = replace(document, tab_ids=(tabs[0].tab_id, tabs[0].tab_id))
    assert plan_tab_names(duplicate_document, (tabs[0], tabs[0])).renames == ()


def test_long_duplicate_titles_remain_bounded_and_repeat_is_noop():
    document = _document("🚀Roadmap " * 20, "🚀Roadmap " * 20)
    tabs = _tabs("Tab 1", "Tab 2")
    plan = plan_tab_names(document, tabs)
    assert plan.renames[1].title.endswith(" (2)")
    assert all(utf16_length(rename.title) <= MAX_TAB_TITLE_UNITS for rename in plan.renames)
    result = tuple(
        replace(tab, title=rename.title) for tab, rename in zip(tabs, plan.renames, strict=True)
    )
    assert plan_tab_names(document, result).renames == ()


def _notes(title: str, *, bold: bool = False) -> DocumentIR:
    document = _document("Delivery notes")
    return replace(
        document,
        title=title,
        blocks=(
            replace(
                document.blocks[0],
                kind=BlockKind.PARAGRAPH,
                level=None,
                authored_emphasis=(("bold", True),) if bold else (),
            ),
        ),
    )


def test_single_headingless_tab_uses_original_title_without_body_edits_and_is_idempotent():
    document = _notes("Project follow-up")
    before = document
    plan = plan_tab_names(document, _tabs("Tab 1"))
    assert [rename.title for rename in plan.renames] == ["Project Follow-Up"]
    assert document == before
    result = (replace(_tabs("Project Follow-Up")[0], icon_emoji="📄"),)
    assert plan.matches_result(result)
    assert plan_tab_names(document, result).renames == ()


def test_real_heading_then_first_authored_bold_lead_have_priority_over_document_title():
    document = _notes("Project follow-up", bold=True)
    assert plan_tab_names(document, _tabs("Tab 1")).renames[0].title == "Delivery Notes"
    document = replace(
        document,
        blocks=(
            document.blocks[0],
            replace(
                document.blocks[0],
                id="b1",
                kind=BlockKind.HEADING,
                level=1,
                text="Roadmap",
            ),
        ),
    )
    assert plan_tab_names(document, _tabs("Tab 1")).renames[0].title == "Roadmap"


@pytest.mark.parametrize("title", ["Document", "Untitled document", "Tab 1", "", "2026-09-04"])
def test_no_purpose_is_invented_for_generic_or_numeric_source_title(title: str):
    assert [rename.title for rename in plan_tab_names(_notes(title), _tabs("Tab 1")).renames] == [
        "Tab 1"
    ]


def test_authored_tab_and_multiple_headingless_tabs_never_receive_document_title_fallback():
    document = _notes("Project follow-up")
    assert [
        rename.title for rename in plan_tab_names(document, _tabs("Personal decisions")).renames
    ] == ["Personal Decisions"]
    document = replace(document, tab_ids=(DocsTabId("t0"), DocsTabId("t1")))
    assert [
        rename.title for rename in plan_tab_names(document, _tabs("Tab 1", "Tab 2")).renames
    ] == ["Tab 1", "Tab 2"]


def test_later_bold_body_prose_and_long_or_protected_leads_are_not_tab_titles():
    document = _notes("Document")
    bold = replace(document.blocks[0], id="b1", authored_emphasis=(("bold", True),))
    assert [
        rename.title
        for rename in plan_tab_names(
            replace(document, blocks=(*document.blocks, bold)), _tabs("Tab 1")
        ).renames
    ] == ["Tab 1"]
    for block in (replace(bold, text="A long body sentence " * 8), replace(bold, editable=False)):
        assert [
            rename.title
            for rename in plan_tab_names(replace(document, blocks=(block,)), _tabs("Tab 1")).renames
        ] == ["Tab 1"]


@pytest.mark.parametrize("title", ["خطة العمل", "🚀 Review " * 20, "Safe\u202e title"])
def test_original_title_fallback_preserves_unicode_and_bounds(title: str):
    document = _notes(title)
    plan = plan_tab_names(document, _tabs("Tab 1"))
    assert len(plan.renames) == 1
    assert utf16_length(plan.renames[0].title) <= MAX_TAB_TITLE_UNITS
    assert "\u202e" not in plan.renames[0].title
    assert (
        plan_tab_names(
            document,
            (replace(_tabs(plan.renames[0].title)[0], icon_emoji=plan.renames[0].icon_emoji),),
        ).renames
        == ()
    )


async def test_copy_tab_naming_receives_original_title_not_generated_output_suffix():
    seen: list[str] = []

    class Gateway(_Gateway):
        @override
        async def plan_tab_naming(
            self,
            *,
            document_id: FileId,
            title: str,
            operation_id: uuid.UUID,
            required_revision_id: DocsRevisionId,
            prefer_document_title: bool = False,
        ) -> None:
            del document_id, operation_id, required_revision_id, prefer_document_title
            seen.append(title)

    gateway = Gateway()
    task_id = TaskId(uuid.uuid7())
    result = await _copy_executor(
        gateway=gateway, store=_CopyCheckpointStore(), task_id=task_id
    ).execute(
        operation_id=task_id,
        request=DocumentTransformRequest(
            source=MarkdownSource(
                kind="markdown",
                content="Rough notes without headings.",
                name="Project follow-up",
                destination_folder_id=FileId("folder"),
            ),
            mode=TransformMode.FORMAT,
        ),
    )
    assert result.output.name == "Project Follow-Up — Sarj Formatted"
    assert seen == ["Project follow-up"]

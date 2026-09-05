from dataclasses import replace

from app.document_transform.ir import BlockKind, DocumentIR, SourceRange, TextBlock, utf16_length
from app.document_transform.tab_names import (
    MAX_TAB_TITLE_UNITS,
    NativeTabProperties,
    plan_tab_names,
)
from app.identifiers import DocsTabId


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
    assert [rename.title for rename in plan.renames] == ["Risks and mitigations", "Project roadmap"]
    assert plan.renames[0].request() == {
        "updateDocumentTabProperties": {
            "tabProperties": {"tabId": "t0", "title": "Risks and mitigations"},
            "fields": "title",
        },
    }
    assert plan.matches_preimage(tabs)
    result = tuple(
        replace(tab, title=rename.title) for tab, rename in zip(tabs, plan.renames, strict=True)
    )
    assert plan.matches_result(result)
    assert not plan.matches_preimage(result)
    assert plan_tab_names(document, result).renames == ()


def test_authored_titles_are_preserved_and_collisions_are_bounded():
    document = _document("Risks", "Risks", "RISKS")
    plan = plan_tab_names(document, _tabs("Risks", "Tab 2", "Tab 3"))
    assert [rename.title for rename in plan.renames] == ["Risks (2)", "RISKS (3)"]


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
    assert plan_tab_names(document, _tabs("Tab 1", "Tab 2", "Tab 3")).renames == ()


def test_rtl_and_astral_titles_are_bounded_and_control_characters_removed():
    document = _document("خطة العمل", "🚀 Launch " * 15, "Safe\u202e title\n name")
    plan = plan_tab_names(document, _tabs("", "Tab 2", "Tab 3"))
    assert plan.renames[0].title == "خطة العمل"
    assert utf16_length(plan.renames[1].title) <= MAX_TAB_TITLE_UNITS
    assert plan.renames[2].title == "Safe title name"


def test_exact_result_verification_rejects_concurrent_rename_or_topology_change():
    document = _document("Roadmap", "Risk register")
    tabs = _tabs("Tab 1", "Existing author title")
    plan = plan_tab_names(document, tabs)
    result = (replace(tabs[0], title="Roadmap"), tabs[1])
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
